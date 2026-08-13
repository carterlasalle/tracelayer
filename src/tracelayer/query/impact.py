"""Impact queries (FR-015, §Q impact.py).

Impact answers "what breaks if I change this node?".  Declared edges point
from the produced artifact to its cause, so dependents sit at the *other*
end: the walk follows incoming edges (direction="in") — implementations that
satisfy a changed requirement, tests that exercise a changed implementation,
callers of a changed symbol.  All traversals go through
graph.traverse.bounded_walk (depth and node caps).
"""

from __future__ import annotations

from dataclasses import dataclass

from tracelayer.git.history import file_history
from tracelayer.git.repo import GitRepo
from tracelayer.graph.models import Node
from tracelayer.graph.store import GraphStore
from tracelayer.graph.traverse import bounded_walk
from tracelayer.protocol.ontology import SEMANTIC_EDGES, STRUCTURAL_EDGES

# Bounds: node cap passed to bounded_walk; history length cap.
_MAX_WALK_NODES = 500
_MAX_HISTORY = 200


@dataclass
class ImpactResult:
    semantic: list[Node]  # declared downstream dependents
    structural: list[Node]  # structural downstream (calls/imports)
    tests: list[Node]  # test dependents
    stale: list[tuple[Node, str]]  # affected nodes with non-current status
    history: list  # list[CommitInfo] when include_history


def impact(
    store: GraphStore,
    gitrepo: GitRepo | None,
    trace_id: str,
    *,
    semantic_only: bool = False,
    include_structural: bool = False,
    include_tests: bool = True,
    include_history: bool = False,
    depth: int = 3,
) -> ImpactResult:
    """Impact summary for ``trace_id``; empty lists for unknown ids.

    ``semantic_only`` suppresses structural, test, and history output
    (spec 28.5).  A node counts as stale when its stored status is not
    ``current`` (stale_review_required / reviewed_needs_verification /
    retired).  History covers the target node's own file (bounded), which
    keeps the result deterministic and cheap.
    """
    node = store.get_node(trace_id=trace_id)
    if node is None:
        return ImpactResult(semantic=[], structural=[], tests=[], stale=[], history=[])
    uid = node.entity_uid

    if semantic_only:
        include_structural = False
        include_tests = False
        include_history = False

    walk = bounded_walk(
        store,
        uid,
        direction="in",
        predicates=sorted(SEMANTIC_EDGES),
        depth=depth,
        max_nodes=_MAX_WALK_NODES,
    )
    semantic = sorted(
        (n for n in walk.nodes.values() if n.entity_uid != uid), key=lambda n: n.trace_id
    )

    tests: list[Node] = []
    if include_tests:
        tests = sorted((n for n in semantic if n.node_type == "test"), key=lambda n: n.trace_id)

    structural: list[Node] = []
    if include_structural:
        swalk = bounded_walk(
            store,
            uid,
            direction="in",
            predicates=sorted(STRUCTURAL_EDGES),
            depth=depth,
            max_nodes=_MAX_WALK_NODES,
        )
        structural = sorted(
            (n for n in swalk.nodes.values() if n.entity_uid != uid), key=lambda n: n.trace_id
        )

    stale: list[tuple[Node, str]] = []
    seen: set[str] = set()
    for n in semantic + structural + tests:
        if n.entity_uid in seen:
            continue
        seen.add(n.entity_uid)
        status = n.status()
        if status != "current":
            stale.append((n, status))
    stale.sort(key=lambda item: item[0].trace_id)

    history: list = []
    if include_history and gitrepo is not None and node.canonical_path:
        history = file_history(gitrepo, node.canonical_path, max_count=_MAX_HISTORY)

    return ImpactResult(
        semantic=semantic,
        structural=structural,
        tests=tests,
        stale=stale,
        history=history,
    )
