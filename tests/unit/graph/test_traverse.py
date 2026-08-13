"""bounded_walk unit tests: bounded walk, cycle termination, depth and
node-count caps, direction and predicate filters."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.graph.traverse import Subgraph, bounded_walk


def _node(trace_id: str) -> Node:
    return Node(
        entity_uid=entity_uid(trace_id),
        trace_id=trace_id,
        node_type="implementation" if trace_id.startswith("IMPL") else "requirement",
        source_kind="declared",
        title=trace_id,
        last_indexed_at="2024-01-01T00:00:00Z",
    )


def _edge(a: str, pred: str, b: str) -> Edge:
    return Edge(
        edge_uid="u",
        from_uid=entity_uid(a),
        predicate=pred,
        to_uid=entity_uid(b),
        source_kind="declared",
    )


def _seed(store: GraphStore, traces: list[str], edges: list[Edge]) -> None:
    store.replace_all([_node(t) for t in traces], edges)


def _uids(sub: Subgraph) -> set[str]:
    return {node.trace_id for node in sub.nodes.values()}


def test_bounded_walk_out_linear_chain(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        _seed(store, ["A", "B", "C", "D"], [_edge("A", "work", "B"), _edge("B", "work", "C")])
        sub = bounded_walk(store, entity_uid("A"))
        assert sub.nodes[entity_uid("A")].trace_id == "A"
        assert _uids(sub) == {"A", "B", "C"}
        assert len(sub.edges) == 2
        # D is not reachable from A.
        assert "D" not in _uids(sub)
    finally:
        store.close()


def test_bounded_walk_cycle_terminates(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        _seed(store, ["A", "B"], [_edge("A", "work", "B"), _edge("B", "work", "A")])
        sub = bounded_walk(store, entity_uid("A"), depth=20, max_nodes=500)
        assert _uids(sub) == {"A", "B"}
        # Both cycle edges collected exactly once each.
        assert len(sub.edges) == 2
    finally:
        store.close()


def test_bounded_walk_self_loop_terminates(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        _seed(store, ["A"], [_edge("A", "satisfies", "A")])
        sub = bounded_walk(store, entity_uid("A"), depth=10)
        assert _uids(sub) == {"A"}
        assert len(sub.edges) == 1
    finally:
        store.close()


def test_bounded_walk_depth_cap(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        edges = [_edge(f"N{i}", "work", f"N{i+1}") for i in range(5)]
        _seed(store, [f"N{i}" for i in range(6)], edges)
        sub0 = bounded_walk(store, entity_uid("N0"), depth=0)
        assert _uids(sub0) == {"N0"}
        sub2 = bounded_walk(store, entity_uid("N0"), depth=2)
        assert _uids(sub2) == {"N0", "N1", "N2"}
        sub5 = bounded_walk(store, entity_uid("N0"), depth=5)
        assert _uids(sub5) == {f"N{i}" for i in range(6)}
    finally:
        store.close()


def test_bounded_walk_max_nodes_cap(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        edges = [_edge(f"N{i}", "work", f"N{i+1}") for i in range(9)]
        _seed(store, [f"N{i}" for i in range(10)], edges)
        sub = bounded_walk(store, entity_uid("N0"), depth=10, max_nodes=3)
        assert len(sub.nodes) == 3
        assert "N3" not in _uids(sub)
    finally:
        store.close()


def test_bounded_walk_direction_and_predicates(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        _seed(
            store,
            ["A", "B", "C", "D"],
            [
                _edge("A", "work", "B"),
                _edge("A", "satisfies", "C"),
                _edge("D", "work", "A"),
            ],
        )
        # Predicate filter keeps only matching edges.
        sub = bounded_walk(store, entity_uid("A"), predicates=["work"])
        assert _uids(sub) == {"A", "B"}
        # Inward walk discovers the requester and its own requester (D->A).
        sub_in = bounded_walk(store, entity_uid("B"), direction="in")
        assert _uids(sub_in) == {"A", "B", "D"}
        # Both directions.
        sub_both = bounded_walk(store, entity_uid("A"), direction="both", depth=1)
        assert _uids(sub_both) == {"A", "B", "C", "D"}
    finally:
        store.close()


def test_bounded_walk_missing_start_returns_empty(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        sub = bounded_walk(store, entity_uid("NOPE"))
        assert sub.nodes == {}
        assert sub.edges == []
    finally:
        store.close()


def test_bounded_walk_invalid_arguments(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(ValueError):
            bounded_walk(store, entity_uid("A"), direction="sideways")
        with pytest.raises(ValueError):
            bounded_walk(store, entity_uid("A"), depth=-1)
        with pytest.raises(ValueError):
            bounded_walk(store, entity_uid("A"), max_nodes=0)
    finally:
        store.close()
