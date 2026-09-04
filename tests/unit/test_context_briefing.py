"""Tests for the engineering briefing context (spec Sections 44, 49)."""

from __future__ import annotations

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.query.context import build_context, render_context_text


def make_node(trace_id: str, node_type: str, **kw) -> Node:
    meta = dict(kw.pop("metadata", {}))
    return Node(
        entity_uid=entity_uid(trace_id),
        trace_id=trace_id,
        node_type=node_type,
        source_kind="declared",
        title=trace_id,
        metadata=meta,
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


# trace:v1 id=test.context.briefing type=test verifies=REQ-engineering-briefing-context
def test_briefing_shows_workflow_and_state(tmp_path) -> None:
    nodes = [
        make_node("WORK-W", "work"),
        make_node("TASK-1", "task", metadata={"state": "PARTIALLY_COMPLETE"}),
        make_node("Q-1", "question", metadata={"state": "OPEN"}),
    ]
    edges = [
        make_edge("TASK-1", "work", "WORK-W"),
        make_edge("Q-1", "work", "WORK-W"),
        make_edge("TASK-1", "blocked_by", "Q-1"),
    ]
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=False)
    try:
        store.replace_all(nodes, edges)
        ctx = build_context(store, None, "TASK-1", root=tmp_path)
    finally:
        store.close()
    assert ctx is not None
    assert ("Blocked by", entity_uid("Q-1")) == (ctx.related[0][0], ctx.related[0][1].entity_uid)
    text = render_context_text(ctx)
    assert "Blocked by:" in text
    assert "Q-1 [OPEN]" in text


# trace:v1 id=test.context.adjacent type=test verifies=REQ-engineering-briefing-context
def test_adjacent_captures_comments_and_excerpt(tmp_path) -> None:
    src = tmp_path / "svc.py"
    src.write_text(
        "# Reuse means the family may have been stolen.\n"
        "# Therefore revoke every member.\n"
        "def rotate(token):\n"
        '    """Rotate and detect replay."""\n'
        "    return token\n",
        encoding="utf-8",
    )
    node = make_node(
        "impl.svc.rotate",
        "implementation",
        canonical_path="svc.py",
        source_start_line=3,
        source_end_line=5,
    )
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=False)
    try:
        store.replace_all([node], [])
        ctx = build_context(store, None, "impl.svc.rotate", root=tmp_path)
    finally:
        store.close()
    assert ctx is not None
    assert ctx.adjacent["leading_comments"] == [
        "# Reuse means the family may have been stolen.",
        "# Therefore revoke every member.",
    ]
    assert "def rotate(token):" in ctx.adjacent["excerpt"]
    assert "Nearby context:" in render_context_text(ctx)
