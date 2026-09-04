"""Tests for the Beads task mirror and reconciliation (spec Sections 33-39)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tracelayer.beads import bd_beads, mirror_tasks, read_mirror, reconcile, run_bd
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid

needs_bd = pytest.mark.skipif(shutil.which("bd") is None, reason="bd CLI not installed")


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


@pytest.fixture()
def beads_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("beads-repo")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    rc, _, err = run_bd(root, "init", "--stealth")
    assert rc == 0, err
    yield root


def make_store(tmp_path, name="s.sqlite3") -> GraphStore:
    nodes = [
        make_node("WORK-W", "work"),
        make_node("TASK-1", "task", metadata={"state": "TODO"}),
        make_node("TASK-2", "task", metadata={"state": "TODO"}),
        make_node("TASK-3", "task", metadata={"state": "DONE"}),
        make_node("Q-1", "question", metadata={"state": "OPEN"}),
    ]
    edges = [
        make_edge("TASK-1", "work", "WORK-W"),
        make_edge("TASK-2", "work", "WORK-W"),
        make_edge("TASK-3", "work", "WORK-W"),
        make_edge("Q-1", "work", "WORK-W"),
        make_edge("TASK-2", "blocked_by", "TASK-1"),
        make_edge("TASK-2", "asks", "Q-1"),
    ]
    store = GraphStore.open(tmp_path / name, fts=False)
    store.replace_all(nodes, edges)
    return store


# trace:v1 id=test.beads.mirror-preview type=test verifies=REQ-task-mirror-with-mapping
@needs_bd
def test_mirror_preview_then_apply(beads_repo, tmp_path) -> None:
    store = make_store(tmp_path, "preview.sqlite3")
    try:
        preview = mirror_tasks(store, beads_repo, "WORK-W")
        assert len(preview["created"]) == 3
        assert all(c.get("preview") for c in preview["created"])
        assert preview["linked"] == [] and preview["skipped"] == []
        assert read_mirror(beads_repo) == {}
        applied = mirror_tasks(store, beads_repo, "WORK-W", apply=True)
        assert len(applied["created"]) == 3
        assert all(c.get("bead") for c in applied["created"])
        assert read_mirror(beads_repo).keys() == {"TASK-1", "TASK-2", "TASK-3"}
        again = mirror_tasks(store, beads_repo, "WORK-W", apply=True)
        assert again["created"] == [] and len(again["skipped"]) == 3
    finally:
        store.close()


# trace:v1 id=test.beads.links type=test verifies=REQ-task-mirror-with-mapping
@needs_bd
def test_mirror_links_blockers(beads_repo, tmp_path) -> None:
    store = make_store(tmp_path, "links.sqlite3")
    try:
        applied = mirror_tasks(store, beads_repo, "WORK-W", apply=True)
        by_task = {c["task"]: c["bead"] for c in applied["created"]}
        assert {
            "task": "TASK-2",
            "bead": by_task["TASK-2"],
            "blocks": by_task["TASK-1"],
            "type": "blocks",
        } in applied["linked"]
        beads = {b["id"]: b for b in bd_beads(beads_repo)}
        assert beads[by_task["TASK-3"]]["status"] == "closed"
    finally:
        store.close()


# trace:v1 id=test.beads.reconcile type=test verifies=REQ-completion-reconciliation
@needs_bd
def test_reconcile_flags_mismatch(beads_repo, tmp_path) -> None:
    store = make_store(tmp_path, "rec.sqlite3")
    try:
        applied = mirror_tasks(store, beads_repo, "WORK-W", apply=True)
        by_task = {c["task"]: c["bead"] for c in applied["created"]}
        rc, _, err = run_bd(beads_repo, "close", by_task["TASK-1"])
        assert rc == 0, err
        result = reconcile(store, beads_repo, "WORK-W")
        assert result["complete"] is False
        assert {
            "task": "TASK-1",
            "bead": by_task["TASK-1"],
            "issue": "closed in Beads but TraceLayer state is TODO",
        } in result["mismatches"]
        assert [q["task"] for q in result["question_blocked"]] == ["TASK-2"]
    finally:
        store.close()
