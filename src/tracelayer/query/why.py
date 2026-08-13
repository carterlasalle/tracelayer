"""Causal/provenance path queries (FR-014, §Q why.py).

A ``why`` path explains why a node exists by walking declared causal edges
backward from the target: implementation -> work (``work``), implementation
-> requirement (``satisfies``), work/decision -> requirement (``addresses``),
plan -> decision (``derived_from``), implementation -> plan (``implements``).
Declared edges always point from the produced artifact to its cause, so the
causal walk follows outgoing edges.

Path format: each path is a list of ``(edge, node)`` hops ordered from the
causal root to the target.  The root is the node of the first hop; the
target is the far endpoint of the last hop's edge (the queried trace id);
the full node chain is ``[n for _, n in path]`` followed by the target.
"""

from __future__ import annotations

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore

# Causal predicates in exploration preference order (§Q why.py).
_PREFERRED_PREDICATES = ("work", "satisfies", "implements", "addresses", "derived_from")
_PREDICATE_RANK = {p: i for i, p in enumerate(_PREFERRED_PREDICATES)}

# Preferred node-type chain (FR-014); presence bonus: work > requirement >
# decision > plan.
_PREFERRED_TYPES = ("work", "requirement", "decision", "plan", "implementation")
_TYPE_POSITION = {t: i for i, t in enumerate(_PREFERRED_TYPES)}
_TYPE_PRESENCE = {"work": 100, "requirement": 90, "decision": 80, "plan": 70}

# Hard bounds: every query terminates and returns at most max_paths paths.
_MAX_DEPTH = 8          # maximum hops per path
_MAX_EXPANSIONS = 200   # maximum nodes explored per query
_CANDIDATE_FACTOR = 4   # collect max_paths * factor candidates before ranking


def _predecessors(store: GraphStore, uid: str) -> list[tuple[Edge, str]]:
    """Causally earlier neighbors of ``uid`` via outgoing preferred edges.

    Sorted by predicate preference (work before satisfies before implements,
    ...) then neighbor uid, so the walk is deterministic.
    """
    preds: list[tuple[Edge, str]] = []
    for edge in store.edges_from(uid):
        if edge.predicate in _PREDICATE_RANK and edge.to_uid != uid:
            preds.append((edge, edge.to_uid))
    preds.sort(key=lambda p: (_PREDICATE_RANK[p[0].predicate], p[1]))
    return preds


def _paths_from(store: GraphStore, node: Node, on_stack: set[str],
                expansions: list[int], cap: int) -> list[list[tuple[Edge, Node]]]:
    """Hop lists from ``node`` down to a causal root, bounded.

    Each returned list is in arrival order (closest to the target first);
    callers reverse it to get root -> target order.  ``on_stack`` guards
    cycles; ``expansions[0]`` bounds total work.  A path that hits the depth
    or expansion bound is truncated at that node (treated as a pseudo-root).
    """
    if len(on_stack) > _MAX_DEPTH or expansions[0] >= _MAX_EXPANSIONS:
        return [[]]
    results: list[list[tuple[Edge, Node]]] = []
    for edge, neighbor_uid in _predecessors(store, node.entity_uid):
        if neighbor_uid in on_stack:
            continue
        neighbor = store.get_node(uid=neighbor_uid)
        if neighbor is None:
            continue  # dangling declared edge (TL002); skip deterministically
        expansions[0] += 1
        on_stack.add(neighbor_uid)
        for suffix in _paths_from(store, neighbor, on_stack, expansions, cap):
            results.append([(edge, neighbor)] + suffix)
            if len(results) >= cap:
                break
        on_stack.discard(neighbor_uid)
        if len(results) >= cap:
            break
    if not results:
        results.append([])  # leaf: terminate the path here
    return results


def _score(path: list[tuple[Edge, Node]], target: Node) -> tuple[int, int, str]:
    """Rank key: prefer work -> requirement -> decision -> plan chains
    (FR-014), then brevity, then a deterministic trace-id tiebreak."""
    nodes = [n for _, n in path] + [target]
    types = [n.node_type for n in nodes]
    score = sum(_TYPE_PRESENCE.get(t, 0) for t in types)
    positions = [_TYPE_POSITION.get(t, len(_TYPE_POSITION)) for t in types]
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            if positions[i] < positions[j]:
                score += 2  # pair appears in preferred order
    score -= len(path)  # prefer shorter paths of equal quality
    chain = "-".join(n.trace_id for n in nodes)
    return (-score, len(path), chain)


def why_paths(store: GraphStore, trace_id: str, *,
              max_paths: int = 5) -> list[list[tuple[Edge, Node]]]:
    """Return up to ``max_paths`` causal paths ending at ``trace_id``.

    Unknown ids and nodes with no causal predecessors yield [].  Paths are
    ranked by how well they match the preferred work -> requirement ->
    decision -> plan chain (FR-014); all work is bounded by _MAX_DEPTH,
    _MAX_EXPANSIONS, and max_paths.
    """
    target = store.get_node(trace_id=trace_id)
    if target is None:
        return []
    cap = max(1, max_paths) * _CANDIDATE_FACTOR
    arrivals = _paths_from(store, target, {target.entity_uid}, [0], cap)
    paths = [list(reversed(a)) for a in arrivals if a]
    seen: set[tuple[str, ...]] = set()
    unique: list[list[tuple[Edge, Node]]] = []
    for path in paths:
        key = tuple(n.trace_id for _, n in path) + (trace_id,)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    unique.sort(key=lambda p: _score(p, target))
    return unique[: max(1, max_paths)]
