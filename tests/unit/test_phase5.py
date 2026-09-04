"""Tests for global setup surface, filesystem registry, and web work view (Phase 5)."""

from __future__ import annotations

from types import SimpleNamespace

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.paths import class_info, gitignore_gaps, path_classes
from tracelayer.web import work_payload


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


# trace:v1 id=test.paths.registry type=test verifies=REQ-filesystem-classification-registry
def test_registry_covers_hygiene_classes() -> None:
    assert set(path_classes()) == {
        "CANONICAL_SOURCE",
        "TRACKED_PROJECT_STATE",
        "LOCAL_PROJECT_STATE",
        "GLOBAL_AGENT_INSTALLATION",
        "CACHE",
        "GENERATED_OUTPUT",
        "TEST_OUTPUT",
        "BUILD_OUTPUT",
        "EXTERNAL_TOOL_STATE",
    }
    assert class_info("cache")["tracked"] is False  # type: ignore[index]
    assert class_info("bogus") is None


# trace:v1 id=test.paths.dogfood type=test verifies=REQ-filesystem-classification-registry
def test_own_repo_has_no_gaps() -> None:
    from pathlib import Path

    assert gitignore_gaps(Path(__file__).resolve().parent.parent.parent) == []


# trace:v1 id=test.paths.gaps type=test verifies=REQ-filesystem-classification-registry
def test_gaps_reported_for_bare_repo(tmp_path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    gaps = gitignore_gaps(tmp_path)
    assert ".trace/cache/" in gaps
    assert "node_modules/" not in gaps


# trace:v1 id=test.web.work-ready type=test verifies=REQ-web-work-view-data
def test_work_payload_ready_and_unknown(tmp_path) -> None:
    nodes = [
        make_node("WORK-W", "work"),
        make_node("TASK-1", "task", metadata={"state": "TODO"}),
    ]
    edges = [
        Edge(
            edge_uid="",
            from_uid=entity_uid("TASK-1"),
            predicate="work",
            to_uid=entity_uid("WORK-W"),
            source_kind="declared",
        )
    ]
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=False)
    try:
        store.replace_all(nodes, edges)
        engine = SimpleNamespace(store=store)
        payload = work_payload(engine, "WORK-W")  # type: ignore[arg-type]
        assert payload is not None
        assert payload["ready"] == ["TASK-1"]
        assert work_payload(engine, "WORK-MISSING") is None  # type: ignore[arg-type]
    finally:
        store.close()
