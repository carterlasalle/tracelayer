"""GraphStore unit tests: CRUD, atomic rebuild, deterministic UIDs,
diagnostics round-trip, artifact versions, evidence tables, FTS search,
and stats."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tracelayer.diagnostics import make
from tracelayer.evidence import models as em
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import (
    GraphStore,
    diagnostic_uid,
    edge_uid,
    entity_uid,
)


# trace:v1 id=test.dogfood.tests.unit.graph.test_store.py type=test
def _node(
    trace_id: str,
    *,
    node_type: str = "requirement",
    title: str | None = None,
    metadata: dict | None = None,
    **kw,
) -> Node:
    return Node(
        entity_uid="ignored-wrong-uid",
        trace_id=trace_id,
        node_type=node_type,
        source_kind="declared",
        title=title or trace_id,
        canonical_path=kw.pop("canonical_path", None),
        metadata=metadata or {},
        last_indexed_at="2024-01-01T00:00:00Z",
        **kw,
    )


def _edge(
    from_uid: str,
    predicate: str,
    to_uid: str,
    *,
    source_kind: str = "declared",
    source_path: str | None = "src/a.py",
    source_line: int | None = 3,
    **kw,
) -> Edge:
    return Edge(
        edge_uid="ignored-wrong-uid",
        from_uid=from_uid,
        predicate=predicate,
        to_uid=to_uid,
        source_kind=source_kind,
        source_path=source_path,
        source_line=source_line,
        **kw,
    )


# ---------------------------------------------------------------- open/close


def test_open_creates_db_in_wal_mode(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "index.sqlite3", fts=True)
    try:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        assert (tmp_path / "index.sqlite3").exists()
    finally:
        store.close()


def test_open_without_fts(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "index.sqlite3", fts=False)
    try:
        assert store._fts is False
        # A store opened without FTS still serves nodes.
        store.replace_all([_node("REQ-1")], [])
        assert store.get_node(trace_id="REQ-1") is not None
    finally:
        store.close()


# ------------------------------------------------------------ CRUD + UIDs


def test_replace_all_and_get_node_by_uid_or_trace_id(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        n = _node("REQ-1", node_type="requirement", title="Auth")
        store.replace_all([n], [])
        by_uid = store.get_node(uid=entity_uid("REQ-1"))
        by_trace = store.get_node(trace_id="REQ-1")
        assert by_uid is not None and by_trace is not None
        assert by_uid.trace_id == "REQ-1"
        assert by_uid.node_type == "requirement"
        assert by_uid.title == "Auth"
        assert by_uid.active is True
        assert by_trace.entity_uid == entity_uid("REQ-1")
        assert store.get_node(trace_id="MISSING") is None
        assert store.get_node_uid("REQ-1") == entity_uid("REQ-1")
        assert store.get_node_uid("MISSING") is None
        assert store.trace_id_exists("REQ-1") is True
        assert store.trace_id_exists("MISSING") is False
    finally:
        store.close()


def test_get_node_requires_exactly_one_selector(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(ValueError):
            store.get_node()
        with pytest.raises(ValueError):
            store.get_node(uid="a", trace_id="b")
    finally:
        store.close()


def test_uids_are_deterministic_and_canonicalized(tmp_path: Path) -> None:
    """The store recomputes UIDs from the shared scheme; caller-supplied
    (wrong) UIDs are replaced by the canonical ones."""
    a, b = entity_uid("REQ-1"), entity_uid("IMPL-1")
    assert a == "n_" + __import__("hashlib").sha256(b"REQ-1").hexdigest()[:32]
    assert a != b
    # Same inputs -> same uid, stable across calls.
    assert entity_uid("REQ-1") == a
    e = edge_uid(a, "satisfies", b, "declared", "src/a.py", 3)
    assert e == edge_uid(a, "satisfies", b, "declared", "src/a.py", 3)
    assert e != edge_uid(a, "satisfies", b, "declared", "src/a.py", 4)

    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all(
            [_node("REQ-1"), _node("IMPL-1", node_type="implementation")],
            [_edge(a, "satisfies", b)],
        )
        node = store.get_node(trace_id="REQ-1")
        assert node is not None
        assert node.entity_uid == a  # canonical, not "ignored-wrong-uid"
        edge = store.all_edges()[0]
        assert edge.edge_uid == e
    finally:
        store.close()


def test_replace_all_is_atomic_on_failure(tmp_path: Path) -> None:
    """A failing replace_all rolls back the whole transaction: the previous
    graph must remain intact (no partial wipe/insert)."""
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all([_node("REQ-1"), _node("IMPL-1", node_type="implementation")], [])
        # last_indexed_at is NOT NULL -> this insert fails mid-transaction,
        # after the wipe has already run inside the same transaction.
        bad = Node(
            entity_uid="x",
            trace_id="BAD-1",
            node_type="requirement",
            source_kind="declared",
            last_indexed_at=None,
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.replace_all([bad], [])
        # The original graph is untouched.
        assert store.get_node(trace_id="REQ-1") is not None
        assert store.get_node(trace_id="IMPL-1") is not None
        assert store.get_node(trace_id="BAD-1") is None
        assert {n.trace_id for n in store.all_nodes()} == {"REQ-1", "IMPL-1"}
    finally:
        store.close()


def test_replace_all_replaces_existing_content(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all([_node("REQ-1"), _node("OLD-1")], [])
        store.replace_all([_node("REQ-1", title="Updated")], [])
        nodes = store.all_nodes()
        assert [n.trace_id for n in nodes] == ["REQ-1"]
        assert nodes[0].title == "Updated"
    finally:
        store.close()


def test_mark_inactive_except_and_active_filter(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all(
            [_node("A"), _node("B"), _node("C")],
            [],
        )
        store.mark_inactive_except({entity_uid("A"), entity_uid("B")})
        assert [n.trace_id for n in store.all_nodes(active_only=True)] == ["A", "B"]
        all_nodes = store.all_nodes(active_only=False)
        assert {n.trace_id for n in all_nodes} == {"A", "B", "C"}
        assert {n.active for n in all_nodes} == {True, False}
        # Empty set marks everything inactive.
        store.mark_inactive_except(set())
        assert store.all_nodes(active_only=True) == []
    finally:
        store.close()


def test_set_node_meta_and_fingerprint(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all([_node("REQ-1", metadata={"status": "current"})], [])
        store.set_node_meta("REQ-1", "summary", "rotation")
        store.set_node_meta("REQ-1", "status", "stale_review_required")
        node = store.get_node(trace_id="REQ-1")
        assert node is not None
        assert node.metadata == {"status": "stale_review_required", "summary": "rotation"}
        assert node.status() == "stale_review_required"
        # No-op for missing trace id.
        store.set_node_meta("MISSING", "k", "v")
        assert store.get_node(trace_id="MISSING") is None

        store.set_node_fingerprint("REQ-1", "fp-abc")
        assert store.current_fingerprint("REQ-1") == "fp-abc"
        assert store.current_fingerprint("MISSING") is None
    finally:
        store.close()


def test_edges_crud_and_status(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        a, b, c = entity_uid("A"), entity_uid("B"), entity_uid("C")
        store.replace_all(
            [_node("A"), _node("B"), _node("C")],
            [
                _edge(a, "satisfies", b),
                _edge(a, "work", c, source_kind="declared", source_path="src/x.py", source_line=1),
                _edge(
                    b, "calls", c, source_kind="structural", source_path="src/x.py", source_line=9
                ),
            ],
        )
        assert len(store.all_edges()) == 3
        out = store.edges_from(a)
        assert {e.predicate for e in out} == {"satisfies", "work"}
        assert [e.predicate for e in store.edges_from(a, predicate="work")] == ["work"]
        assert [e.predicate for e in store.edges_to(c)] == ["work", "calls"]
        assert store.edges_to(c, predicate="work")[0].from_uid == a
        assert store.edges_from(c) == []
        assert store.edges_to(a) == []

        eid = store.all_edges()[0].edge_uid  # A->B satisfies
        store.set_edge_status(eid, "historical")
        assert store.all_edges(status="historical")[0].edge_uid == eid
        assert len(store.all_edges(status="active")) == 2

        store.set_edge_statuses_for_node(b, "stale_review_required")
        # Every edge touching b flips; the A->C and B->C edges too.
        for e in store.all_edges():
            if e.from_uid == b or e.to_uid == b:
                assert e.status == "stale_review_required"
        # A->C satisfies does not touch b and keeps its status.
        ac = store.edges_from(a, predicate="work")[0]
        assert ac.status == "active"
    finally:
        store.close()


# ------------------------------------------------------------- diagnostics


def test_diagnostics_roundtrip(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        d1 = make(
            "TL002",
            trace_id="IMPL-1",
            path="src/a.py",
            line=4,
            message="unresolved target",
            metadata={"extra": 1},
        )
        d2 = make("TL003", severity="WARNING", message="detached")
        store.insert_diagnostics([d1, d2])
        all_diags = store.get_diagnostics()
        assert len(all_diags) == 2
        assert all_diags[0] == d1
        assert all_diags[0].trace_id == "IMPL-1"
        assert all_diags[0].path == "src/a.py"
        assert all_diags[0].line == 4
        assert all_diags[0].metadata == {"extra": 1}
        assert all_diags[1].rule_id == "TL003"
        assert all_diags[1].severity == "WARNING"

        assert [d.rule_id for d in store.get_diagnostics(rule_id="TL002")] == ["TL002"]
        assert [d.rule_id for d in store.get_diagnostics(severity="WARNING")] == ["TL003"]
        # d1 is TL002 + ERROR, so it matches the combined filter.
        assert [d.rule_id for d in store.get_diagnostics(severity="ERROR", rule_id="TL002")] == [
            "TL002"
        ]
        assert store.get_diagnostics(severity="ERROR", rule_id="TL003") == []
    finally:
        store.close()


def test_diagnostics_deduplicated_by_uid(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        d = make("TL002", trace_id="T", path="p", line=1, message="m")
        store.insert_diagnostics([d, d])
        assert len(store.get_diagnostics()) == 1
    finally:
        store.close()


def test_replace_diagnostics_wipes_and_inserts(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.insert_diagnostics([make("TL002", message="old")])
        store.replace_diagnostics([make("TL003", message="new")])
        diags = store.get_diagnostics()
        assert len(diags) == 1
        assert diags[0].rule_id == "TL003"
    finally:
        store.close()


def test_diagnostic_uid_deterministic() -> None:
    u1 = diagnostic_uid("TL002", "T", "p", 1, "m")
    u2 = diagnostic_uid("TL002", "T", "p", 1, "m")
    assert u1 == u2
    assert u1.startswith("d_")
    assert u1 != diagnostic_uid("TL003", "T", "p", 1, "m")
    assert u1 != diagnostic_uid("TL002", "T", "p", 2, "m")


def test_replace_all_clears_diagnostics_and_fts(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.insert_diagnostics([make("TL002", message="stale")])
        store.replace_all([_node("REQ-1", title="Fresh")], [])
        assert store.get_diagnostics() == []
        assert [n.trace_id for n in store.search("Fresh")] == ["REQ-1"]
        # Old FTS content gone.
        assert store.search("stale") == []
    finally:
        store.close()


# ------------------------------------------------------- artifact versions


def test_artifact_versions_ordering(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all([_node("REQ-1")], [])
        store.record_artifact_version("REQ-1", "fp-1", "rev1", "src/a.md", "2024-01-01T00:00:00Z")
        store.record_artifact_version("REQ-1", "fp-3", "rev3", "src/a.md", "2024-01-03T00:00:00Z")
        store.record_artifact_version("REQ-1", "fp-2", "rev2", "src/a.md", "2024-01-02T00:00:00Z")
        # Oldest first (newest last), independent of insertion order.
        assert store.previous_fingerprints("REQ-1") == ["fp-1", "fp-2", "fp-3"]
        # The current fingerprint comes from the nodes table, not versions.
        store.set_node_fingerprint("REQ-1", "fp-3")
        assert store.current_fingerprint("REQ-1") == "fp-3"
        # Exclude filters one fingerprint out.
        assert store.previous_fingerprints("REQ-1", exclude="fp-2") == ["fp-1", "fp-3"]
        assert store.previous_fingerprints("MISSING") == []
    finally:
        store.close()


# --------------------------------------------------------------- evidence


def test_evidence_runs_and_ordering(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.add_evidence_run(
            "run-1",
            "abc",
            "pytest",
            "ci",
            "2024-01-01T00:00:00Z",
            "2024-01-01T00:01:00Z",
            "pass",
            "junit.xml",
            {"k": "v"},
        )
        store.add_evidence_run(
            "run-2",
            "abc",
            "pytest",
            "ci",
            "2024-01-02T00:00:00Z",
            "2024-01-02T00:01:00Z",
            "fail",
            "junit.xml",
            {},
        )
        store.add_evidence_run(
            "run-3",
            "def",
            "pytest",
            "ci",
            "2024-01-03T00:00:00Z",
            "2024-01-03T00:01:00Z",
            "pass",
            "junit.xml",
            {},
        )
        latest = store.latest_evidence_run()
        assert latest is not None and latest["run_id"] == "run-3"
        latest_abc = store.latest_evidence_run(revision="abc")
        assert latest_abc is not None and latest_abc["run_id"] == "run-2"
        assert store.latest_evidence_run(revision="zzz") is None
        runs = store.get_evidence_runs(revision="abc")
        assert [r["run_id"] for r in runs] == ["run-2", "run-1"]  # newest first
        assert runs[0]["status"] == "fail"
        # Raw rows expose metadata_json (JSON-encoded), not a decoded key.
        assert json.loads(runs[0]["metadata_json"]) == {}
        assert json.loads(runs[1]["metadata_json"]) == {"k": "v"}
        assert store.get_evidence_runs(revision="nope") == []
    finally:
        store.close()


def test_test_results_and_latest_outcome(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.add_evidence_run(
            "run-1", "r1", None, None, "2024-01-01T00:00:00Z", None, "pass", None, {}
        )
        store.add_evidence_run(
            "run-2", "r2", None, None, "2024-01-02T00:00:00Z", None, "pass", None, {}
        )
        store.add_test_results(
            "run-1",
            [
                em.TestOutcome(framework_id="t.a", outcome="pass", test_uid="n_t1"),
                em.TestOutcome(framework_id="t.b", outcome="fail", test_uid="n_t2"),
            ],
        )
        store.add_test_results(
            "run-2", [em.TestOutcome(framework_id="t.a", outcome="fail", test_uid="n_t1")]
        )
        outcomes = store.outcomes_for_run("run-1")
        assert [o.framework_id for o in outcomes] == ["t.a", "t.b"]
        assert outcomes[0].test_uid == "n_t1"
        # Latest across runs is by run start time: run-2's fail.
        latest = store.latest_outcome("t.a")
        assert latest is not None
        assert latest.outcome == "fail"
        assert latest.framework_id == "t.a"
        assert store.latest_outcome("never") is None
    finally:
        store.close()


def test_execution_edges_roundtrip_with_metadata_overlay(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        rec = em.ExecutionRecord(
            run_id="run-1",
            test_uid="n_test",
            implementation_uid="n_impl",
            coverage_kind="per_test",
            hit_count=7,
            confidence=0.9,
            metadata={"behavioral": True},
        )
        store.add_execution_edges("run-1", [rec])
        got = store.execution_edges_for("n_impl")
        assert len(got) == 1
        assert got[0].run_id == "run-1"
        assert got[0].test_uid == "n_test"
        assert got[0].hit_count == 7
        assert got[0].confidence == 0.9
        # In-session metadata overlay round-trips.
        assert got[0].metadata == {"behavioral": True}
        by_test = store.execution_edges_for_test("n_test")
        assert by_test[0].implementation_uid == "n_impl"
        assert store.execution_edges_for("n_other") == []
    finally:
        store.close()


def test_verification_bindings(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.add_verification_binding("ev-1", "n_impl", "fp-1", "r1", "pass")
        store.add_verification_binding("ev-2", "n_impl", "fp-2", "r2", "fail")
        bindings = store.bindings_for("n_impl")
        assert len(bindings) == 2
        by_result = {b["result"]: b for b in bindings}
        assert by_result["pass"]["target_fingerprint"] == "fp-1"
        assert by_result["pass"]["revision"] == "r1"
        assert store.bindings_for("n_other") == []
    finally:
        store.close()


# -------------------------------------------------------------- FTS search


def test_fts_search_matches_id_title_symbol_summary(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=True)
    try:
        store.replace_all(
            [
                _node(
                    "REQ-AUTH-017",
                    node_type="requirement",
                    title="Refresh token rotation",
                    symbol_qualified_name="src.auth.tokens",
                    metadata={"summary": "rotate refresh tokens", "work_label": "auth-work"},
                ),
                _node("IMPL-AUTH-002", node_type="implementation", title="Token store"),
            ],
            [],
        )
        # Full trace id (phrase), token, title word, symbol, summary word.
        assert [n.trace_id for n in store.search("REQ-AUTH-017")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("REQ")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("rotation")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("tokens")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("rotate")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("auth-work")] == ["REQ-AUTH-017"]
        assert store.search("nonexistent-term") == []
        assert store.search("") == []
        assert store.search("   ") == []
    finally:
        store.close()


def test_search_like_fallback_without_fts(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=False)
    try:
        store.replace_all([_node("REQ-AUTH-017", title="Refresh")], [])
        assert [n.trace_id for n in store.search("REQ")] == ["REQ-AUTH-017"]
        assert [n.trace_id for n in store.search("REQ-AUTH-017")] == ["REQ-AUTH-017"]
        assert store.search("zzz") == []
    finally:
        store.close()


def test_search_limit_and_fts_rejection_fallback(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3", fts=True)
    try:
        nodes = [_node(f"REQ-{i:03d}", title=f"req {i}") for i in range(5)]
        store.replace_all(nodes, [])
        assert len(store.search("REQ", limit=2)) == 2
        assert len(store.search("REQ")) == 5
        # "REQ:" is FTS5 column-filter syntax -> OperationalError -> LIKE
        # fallback; nothing matches the literal string.
        assert store.search("REQ:") == []
        # A valid LIKE fallback still works after the FTS path rejects.
        assert [n.trace_id for n in store.search("REQ-001")] == ["REQ-001"]
    finally:
        store.close()


# ------------------------------------------------------------------ stats


def test_stats_counts_and_changed_artifacts(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        store.replace_all(
            [
                _node("REQ-1"),
                _node("IMPL-1", node_type="implementation", metadata={"changed": True}),
                _node(
                    "IMPL-2",
                    node_type="implementation",
                    metadata={"status": "stale_review_required"},
                ),
            ],
            [
                _edge(entity_uid("REQ-1"), "satisfies", entity_uid("IMPL-1")),
                _edge(entity_uid("REQ-1"), "calls", entity_uid("IMPL-2"), source_kind="structural"),
                _edge(
                    entity_uid("REQ-1"), "exercises", entity_uid("IMPL-2"), source_kind="observed"
                ),
            ],
        )
        store.insert_diagnostics([make("TL002", message="m")])
        store.add_evidence_run(
            "run-1", "r1", None, None, "2024-01-01T00:00:00Z", None, "pass", None, {}
        )
        stats = store.stats()
        assert stats["nodes"] == 3
        assert stats["declared_edges"] == 1
        assert stats["structural_edges"] == 1
        assert stats["observed_edges"] == 1
        assert stats["evidence_runs"] == 1
        assert stats["diagnostics"] == 1
        assert stats["changed_artifacts"] == 2  # metadata changed + stale status
    finally:
        store.close()


def test_stats_empty_store(tmp_path: Path) -> None:
    store = GraphStore.open(tmp_path / "s.sqlite3")
    try:
        stats = store.stats()
        assert stats == {
            "nodes": 0,
            "declared_edges": 0,
            "structural_edges": 0,
            "observed_edges": 0,
            "evidence_runs": 0,
            "diagnostics": 0,
            "changed_artifacts": 0,
        }
    finally:
        store.close()
