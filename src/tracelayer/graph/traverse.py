"""Bounded graph traversal (spec §G).

``bounded_walk`` fans out from a start node along edges, honoring direction,
predicate filters, a depth cap, and a node-count cap. A visited set makes the
walk terminate on cyclic graphs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore


@dataclass
class Subgraph:
    """A connected slice of the graph: discovered nodes keyed by uid and edges."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)


def bounded_walk(
    store: GraphStore,
    start_uid: str,
    *,
    direction: str = "out",
    predicates: list[str] | None = None,
    depth: int = 5,
    max_nodes: int = 500,
) -> Subgraph:
    """Breadth-first walk from ``start_uid``.

    ``direction``: ``"out"`` follows outgoing edges, ``"in"`` incoming,
    ``"both"`` both. Only edges whose predicate is in ``predicates`` (all when
    None) are traversed. Discovery stops after ``depth`` hops or once
    ``max_nodes`` nodes have been collected; a visited set guarantees
    termination on cycles. A missing start node yields an empty Subgraph.
    """
    if direction not in ("out", "in", "both"):
        raise ValueError(f"direction must be 'out', 'in', or 'both', got {direction!r}")
    if depth < 0:
        raise ValueError(f"depth must be >= 0, got {depth}")
    if max_nodes < 1:
        raise ValueError(f"max_nodes must be >= 1, got {max_nodes}")

    start = store.get_node(uid=start_uid)
    if start is None:
        return Subgraph()

    sub = Subgraph(nodes={start_uid: start})
    seen_edges: set[str] = set()
    visited: set[str] = set()
    frontier: list[str] = [start_uid]

    for _ in range(depth):
        if not frontier or len(sub.nodes) >= max_nodes:
            break
        visited.update(frontier)
        next_frontier: list[str] = []
        for uid in frontier:
            if len(sub.nodes) >= max_nodes:
                break
            if direction in ("out", "both"):
                for e in store.edges_from(uid):
                    if predicates is not None and e.predicate not in predicates:
                        continue
                    if e.edge_uid not in seen_edges:
                        seen_edges.add(e.edge_uid)
                        sub.edges.append(e)
                    if (
                        e.to_uid not in sub.nodes
                        and e.to_uid not in visited
                        and len(sub.nodes) < max_nodes
                    ):
                        n = store.get_node(uid=e.to_uid)
                        if n is not None:
                            sub.nodes[e.to_uid] = n
                            next_frontier.append(e.to_uid)
            if direction in ("in", "both"):
                for e in store.edges_to(uid):
                    if predicates is not None and e.predicate not in predicates:
                        continue
                    if e.edge_uid not in seen_edges:
                        seen_edges.add(e.edge_uid)
                        sub.edges.append(e)
                    if (
                        e.from_uid not in sub.nodes
                        and e.from_uid not in visited
                        and len(sub.nodes) < max_nodes
                    ):
                        n = store.get_node(uid=e.from_uid)
                        if n is not None:
                            sub.nodes[e.from_uid] = n
                            next_frontier.append(e.from_uid)
        frontier = next_frontier

    return sub
