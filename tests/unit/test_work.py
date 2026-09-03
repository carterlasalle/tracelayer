"""Tests for native work/task/question states and readiness (spec Sections 7, 14, 35)."""

from __future__ import annotations

import pytest

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.protocol.marker import parse_marker_line, render_marker
from tracelayer.work import (
    compute_readiness,
    normalize_question_state,
    normalize_task_state,
    normalize_work_state,
)


def make_node(trace_id: str, node_type: str, state: str | None = None) -> Node:
    return Node(
        entity_uid=entity_uid(trace_id),
        trace_id=trace_id,
        node_type=node_type,
        source_kind="declared",
        title=trace_id,
        metadata={"state": state} if state is not None else {},
        active=True,
        last_indexed_at="2026-01-01T00:00:00Z",
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
    store = GraphStore.open(tmp_path / "store.sqlite3", fts=False)
    store.replace_all(list(nodes), list(edges))
    return store


# trace:v1 id=test.work.state-normalization type=test
def test_normalize_task_states() -> None:
    assert normalize_task_state("TODO") == "TODO"
    assert normalize_task_state("in_progress") == "IN_PROGRESS"
    assert normalize_task_state("half-finished") == "PARTIALLY_COMPLETE"
    assert normalize_task_state("partial") == "PARTIALLY_COMPLETE"
    assert normalize_task_state("unimplemented") == "NOT_IMPLEMENTED"
    assert normalize_task_state("follow-up") == "TODO"
    assert normalize_task_state("waiting for decision") == "WAITING_FOR_DECISION"
    assert normalize_task_state("bogus") == "TODO"
    assert normalize_task_state(None) == "TODO"
    assert normalize_question_state("open") == "OPEN"
    assert normalize_question_state(None) == "OPEN"
    assert normalize_question_state("answered") == "ANSWERED"
    assert normalize_question_state("bogus") == "OPEN"
    assert normalize_work_state(None) == "ACTIVE"
    assert normalize_work_state("partially_complete") == "PARTIALLY_COMPLETE"


# trace:v1 id=test.work.readiness type=test
def test_readiness_ready_blocked_done(tmp_path) -> None:
    nodes = [
        make_node("WORK-W", "work"),
        make_node("TASK-1", "task", "TODO"),
        make_node("TASK-2", "task", "TODO"),
        make_node("TASK-3", "task", "TODO"),
        make_node("TASK-4", "task", "DONE"),
        make_node("TASK-5", "task", "PARTIALLY_COMPLETE"),
    ]
    edges = [
        make_edge("TASK-1", "work", "WORK-W"),
        make_edge("TASK-2", "work", "WORK-W"),
        make_edge("TASK-3", "work", "WORK-W"),
        make_edge("TASK-4", "work", "WORK-W"),
        make_edge("TASK-5", "work", "WORK-W"),
        make_edge("TASK-2", "blocked_by", "TASK-3"),
        make_edge("TASK-3", "blocks", "TASK-5"),
    ]
    store = open_store(tmp_path, nodes, edges)
    try:
        result = compute_readiness(store, "WORK-W")
    finally:
        store.close()
    assert result["ready"] == ["TASK-1", "TASK-3"]
    assert result["done"] == ["TASK-4"]
    assert result["partial"] == ["TASK-5"]
    assert list(result["blocked"]) == ["TASK-2"]
    assert result["blocked"]["TASK-2"] == ["blocked by TASK-3 (TODO)"]


# trace:v1 id=test.work.question-blocking type=test
def test_open_question_blocks_and_answer_unblocks(tmp_path) -> None:
    nodes = [
        make_node("WORK-W", "work"),
        make_node("TASK-6", "task", "TODO"),
        make_node("Q-1", "question", "OPEN"),
    ]
    edges = [
        make_edge("TASK-6", "work", "WORK-W"),
        make_edge("Q-1", "work", "WORK-W"),
        make_edge("TASK-6", "asks", "Q-1"),
    ]
    store = open_store(tmp_path, nodes, edges)
    try:
        blocked = compute_readiness(store, "WORK-W")
        assert blocked["ready"] == []
        assert blocked["blocked"]["TASK-6"] == ["waiting on open question Q-1 (asks)"]
        assert blocked["open_questions"] == ["Q-1"]
        answered = [make_node("WORK-W", "work"), make_node("TASK-6", "task", "TODO"),
                    make_node("Q-1", "question", "ANSWERED")]
        store.replace_all(answered, list(edges))
        ready = compute_readiness(store, "WORK-W")
    finally:
        store.close()
    assert ready["ready"] == ["TASK-6"]
    assert ready["blocked"] == {}
    assert ready["open_questions"] == []


# trace:v1 id=test.work.unknown-work type=test
def test_readiness_unknown_work_raises(tmp_path) -> None:
    store = open_store(tmp_path, [make_node("WORK-W", "work")])
    try:
        with pytest.raises(ValueError, match="no active work node"):
            compute_readiness(store, "WORK-MISSING")
    finally:
        store.close()


# trace:v1 id=test.work.marker-state type=test
def test_marker_state_round_trip() -> None:
    line = "# trace:v1 id=TASK-101 type=task state=PARTIALLY_COMPLETE work=WORK-W"
    parsed = parse_marker_line(line, path="plan.md", line_no=1)
    assert parsed.marker is not None
    assert parsed.marker.properties["state"] == "PARTIALLY_COMPLETE"
    assert "state=PARTIALLY_COMPLETE" in render_marker(parsed.marker)
