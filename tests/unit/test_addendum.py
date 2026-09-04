"""Tests for durable knowledge and canonical facts (addendum Sections 81-124)."""

from __future__ import annotations

import pytest

from tracelayer.facts import read_canonical, verify_facts
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid
from tracelayer.knowledge import knowledge_for, normalize_knowledge_state
from tracelayer.protocol.marker import parse_marker_line, render_marker


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


# trace:v1 id=test.knowledge.query type=test verifies=REQ-knowledge-node-ontology
def test_knowledge_for_ranks_and_filters(tmp_path) -> None:
    nodes = [
        make_node("impl.a", "implementation"),
        make_node("ANTI-1", "anti_pattern", metadata={"state": "ACTIVE"}),
        make_node("LEARN-1", "learning", metadata={"state": "SUPERSEDED"}),
        make_node("CONV-1", "convention", metadata={}),
    ]
    edges = [
        make_edge("ANTI-1", "applies_to", "impl.a"),
        make_edge("LEARN-1", "applies_to", "impl.a"),
        make_edge("CONV-1", "recommended_for", "impl.a"),
    ]
    store = open_store(tmp_path, nodes, edges)
    try:
        items = knowledge_for(store, "impl.a")
        limited = knowledge_for(store, "impl.a", limit=1)
        missing = knowledge_for(store, "impl.unknown")
    finally:
        store.close()
    assert [i["id"] for i in items] == ["ANTI-1", "CONV-1"]
    assert items[0]["state"] == "ACTIVE"
    assert limited == items[:1]
    assert missing == []
    assert normalize_knowledge_state("bogus") == "ACTIVE"


# trace:v1 id=test.facts.canonical type=test verifies=REQ-canonical-fact-tracking
def test_read_canonical_toml_and_json(tmp_path) -> None:
    (tmp_path / "py.toml").write_text('[project]\nversion = "0.2.40"\n', encoding="utf-8")
    (tmp_path / "p.json").write_text('{"a": {"b": 3}}', encoding="utf-8")
    assert read_canonical(tmp_path, "py.toml::project.version") == (True, "0.2.40")
    assert read_canonical(tmp_path, "p.json::a.b") == (True, "3")
    assert read_canonical(tmp_path, "py.toml::project.missing") == (False, "")
    assert read_canonical(tmp_path, "nope.toml::a") == (False, "")
    assert read_canonical(tmp_path, "novalue") == (False, "")


# trace:v1 id=test.facts.verify type=test verifies=REQ-canonical-fact-tracking
def test_verify_facts_detects_drift(tmp_path) -> None:
    (tmp_path / "py.toml").write_text('[project]\nversion = "0.2.40"\n', encoding="utf-8")
    nodes = [
        make_node(
            "VALUE-1",
            "value",
            metadata={"canonical_source": "py.toml::project.version", "value": "0.2.40"},
        ),
        make_node(
            "VALUE-2",
            "value",
            metadata={"canonical_source": "py.toml::project.version", "value": "0.2.39"},
        ),
        make_node("doc.a", "document", metadata={"value": "0.2.40"}),
        make_node("doc.b", "document", metadata={"value": "0.2.39"}),
    ]
    edges = [
        make_edge("doc.a", "documents_value", "VALUE-1"),
        make_edge("doc.b", "documents_value", "VALUE-2"),
    ]
    store = open_store(tmp_path, nodes, edges)
    try:
        results = verify_facts(store, tmp_path)
    finally:
        store.close()
    by_id = {r["id"]: r for r in results}
    assert by_id["VALUE-1"]["status"] == "CURRENT"
    assert by_id["VALUE-1"]["dependents"] == [
        {
            "id": "doc.a",
            "predicate": "documents_value",
            "path": None,
            "status": "CURRENT",
            "observed": "0.2.40",
            "expected": "0.2.40",
        }
    ]
    assert by_id["VALUE-2"]["status"] == "REVIEW_REQUIRED"
    assert by_id["VALUE-2"]["dependents"][0]["status"] == "REVIEW_REQUIRED"


# trace:v1 id=test.facts.live-consumer type=test verifies=REQ-canonical-fact-tracking
def test_stale_readme_without_metadata_is_flagged(tmp_path) -> None:
    (tmp_path / "py.toml").write_text('[project]\nversion = "2.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("Current version: 1.0\n", encoding="utf-8")
    nodes = [
        make_node(
            "VALUE-X",
            "value",
            metadata={"canonical_source": "py.toml::project.version", "value": "2.0"},
        ),
        make_node("doc.readme", "document", metadata={}),
    ]
    edges = [make_edge("doc.readme", "documents_value", "VALUE-X")]
    store = open_store(tmp_path, nodes, edges)
    try:
        results = verify_facts(store, tmp_path)
    finally:
        store.close()
    by_id = {r["id"]: r for r in results}
    assert by_id["VALUE-X"]["status"] == "CURRENT"
    assert by_id["VALUE-X"]["dependents"] == [
        {
            "id": "doc.readme",
            "predicate": "documents_value",
            "path": None,
            "status": "UNVERIFIED",
            "observed": None,
            "expected": "2.0",
        }
    ]


# trace:v1 id=test.facts.readme-claim type=test verifies=REQ-canonical-fact-tracking
def test_readme_claim_with_selector_is_checked_live(tmp_path) -> None:
    (tmp_path / "py.toml").write_text('[project]\nversion = "2.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("Current version: 1.0\n", encoding="utf-8")
    nodes = [
        make_node(
            "VALUE-X",
            "value",
            metadata={"canonical_source": "py.toml::project.version", "value": "2.0"},
        ),
        make_node(
            "doc.readme",
            "document",
            canonical_path="README.md",
            metadata={"selector": "regex:Current version: (\\S+)"},
        ),
    ]
    edges = [make_edge("doc.readme", "documents_value", "VALUE-X")]
    store = open_store(tmp_path, nodes, edges)
    try:
        results = verify_facts(store, tmp_path)
    finally:
        store.close()
    dep = {r["id"]: r for r in results}["VALUE-X"]["dependents"][0]
    assert dep == {
        "id": "doc.readme",
        "predicate": "documents_value",
        "path": "README.md",
        "status": "REVIEW_REQUIRED",
        "observed": "1.0",
        "expected": "2.0",
    }


# trace:v1 id=test.facts.marker type=test verifies=REQ-canonical-fact-tracking
def test_fact_marker_round_trip() -> None:
    line = "# trace:v1 id=VALUE-1 type=value canonical_source=pyproject.toml::project.version value=0.2.40"
    parsed = parse_marker_line(line, path="spec.md", line_no=1)
    assert parsed.marker is not None
    assert parsed.marker.properties["canonical_source"] == "pyproject.toml::project.version"
    rendered = render_marker(parsed.marker)
    assert "canonical_source=pyproject.toml::project.version" in rendered
    assert "value=0.2.40" in rendered


# trace:v1 id=test.facts.confinement type=test verifies=REQ-canonical-fact-tracking
def test_canonical_source_cannot_escape_root(tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": "outside"}', encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "inside.json").write_text('{"ok": 1}', encoding="utf-8")
    assert read_canonical(root, "../outside.json::secret") == (False, "")
    assert read_canonical(root, f"{outside}::secret") == (False, "")
    assert read_canonical(root, "inside.json::ok") == (True, "1")
    link = root / "link.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert read_canonical(root, "link.json::secret") == (False, "")


# trace:v1 id=test.facts.adapters type=test verifies=REQ-canonical-fact-tracking
def test_source_adapters_yaml_python_claims(tmp_path) -> None:
    (tmp_path / "vals.yaml").write_text("server:\n  port: 20128\n", encoding="utf-8")
    assert read_canonical(tmp_path, "vals.yaml::server.port") == (True, "20128")
    (tmp_path / "cfg.py").write_text("DEFAULT_TIMEOUT = 30\n", encoding="utf-8")
    assert read_canonical(tmp_path, "cfg.py::DEFAULT_TIMEOUT") == (True, "30")
    (tmp_path / "cfg.py").write_text('import os\nX = os.environ["Y"]\n', encoding="utf-8")
    assert read_canonical(tmp_path, "cfg.py::X") == (False, "")
    (tmp_path / "notes.md").write_text("Python 3.12 or newer.\n", encoding="utf-8")
    assert read_canonical(tmp_path, r"notes.md::regex:Python (3\.\d+)") == (True, "3.12")
    (tmp_path / "app.env").write_text("PORT=20128\n", encoding="utf-8")
    assert read_canonical(tmp_path, "app.env::PORT") == (True, "20128")
