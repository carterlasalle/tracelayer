"""Tests for harness adapters, Beads detection, and fulfillment (Phase 4)."""

from __future__ import annotations

import pytest

from tracelayer.beads import detect_beads
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.harness import normalize_todos, render_task_blocks
from tracelayer.work import fulfillment


def make_node(trace_id: str, node_type: str, **kw) -> Node:
    return Node(
        entity_uid=entity_uid(trace_id),
        trace_id=trace_id,
        node_type=node_type,
        source_kind="declared",
        title=trace_id,
        metadata=dict(kw.pop("metadata", {})),
        active=True,
        last_indexed_at="2026-01-01T00:00:00Z",
        **kw,
    )


def make_edge(from_id: str, predicate: str, to_id: str) -> Edge:
    return Edge(
        edge_uid="",
        from_uid=entity_uid(from_id),
        predicate=predicate,
        to_uid=entity_uid(to_id),
        source_kind="declared",
    )


def open_store(tmp_path, nodes, edges=()) -> GraphStore:
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=False)
    store.replace_all(list(nodes), list(edges))
    return store


# trace:v1 id=test.harness.normalize type=test
def test_normalize_todos() -> None:
    todos = [
        {"content": "Fix glob matching", "status": "pending", "id": "1"},
        {"content": "Add tests", "status": "in_progress"},
        {"content": "Old idea", "status": "completed"},
        {"content": "   "},
        {"status": "pending"},
    ]
    tasks = normalize_todos("claude", todos)
    assert [(t["title"], t["state"]) for t in tasks] == [
        ("Fix glob matching", "TODO"),
        ("Add tests", "IN_PROGRESS"),
        ("Old idea", "DONE"),
    ]
    assert tasks[0]["origin"] == {"harness": "claude", "ref": "1"}
    assert normalize_todos("omp", [{"content": "X", "status": "blocked"}])[0]["state"] == "BLOCKED"
    assert normalize_todos("codex", [{"content": "X", "status": "weird"}])[0]["state"] == "TODO"
    with pytest.raises(ValueError, match="unknown harness"):
        normalize_todos("jira", [])


# trace:v1 id=test.harness.render type=test
def test_render_task_blocks_mints_unique_ids(tmp_path) -> None:
    store = open_store(tmp_path, [make_node("WORK-W", "work")])
    try:
        tasks = normalize_todos("claude", [{"content": "Fix it", "status": "pending"}])
        blocks = render_task_blocks(store, "WORK-W", tasks)
    finally:
        store.close()
    assert "type=task state=TODO work=WORK-W" in blocks
    assert "## TASK-fix-it — Fix it" in blocks
    assert "harness=claude" in blocks


# trace:v1 id=test.beads.detect type=test
def test_detect_beads_never_initializes(tmp_path) -> None:
    result = detect_beads(tmp_path)
    assert set(result["beads"]) == {"available", "repository_initialized", "active"}
    assert result["beads"]["repository_initialized"] is False
    assert result["beads"]["active"] is False
    assert not (tmp_path / ".beads").exists()
    (tmp_path / ".beads").mkdir()
    assert detect_beads(tmp_path)["beads"]["repository_initialized"] is True
    assert detect_beads(tmp_path, enabled="false")["beads"]["active"] is False


# trace:v1 id=test.work.fulfillment type=test
def test_fulfillment_derives_from_graph(tmp_path) -> None:
    nodes = [
        make_node("REQ-1", "requirement"),
        make_node("REQ-2", "requirement"),
        make_node("REQ-3", "requirement"),
        make_node("impl.a", "implementation"),
        make_node("impl.b", "implementation", metadata={"state": "PARTIAL"}),
    ]
    edges = [make_edge("impl.a", "satisfies", "REQ-2"), make_edge("impl.b", "satisfies", "REQ-3")]
    store = open_store(tmp_path, nodes, edges)
    try:
        assert fulfillment(store, "REQ-1")["status"] == "UNIMPLEMENTED"
        assert fulfillment(store, "REQ-2")["status"] == "IMPLEMENTED"
        assert fulfillment(store, "REQ-3")["status"] == "PARTIALLY_IMPLEMENTED"
        with pytest.raises(ValueError, match="no active requirement"):
            fulfillment(store, "REQ-9")
    finally:
        store.close()
