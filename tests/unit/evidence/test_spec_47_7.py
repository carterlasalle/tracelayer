"""Spec 47.7 CI/evidence scenarios: deleted test trace, renamed tests,
aggregate coverage cannot falsely claim L2 proof."""

from __future__ import annotations

from tests.unit.conftest import make_edge, make_node
from tracelayer.policy.evaluator import evaluate


def _outcome(framework_id: str, outcome: str, test_uid: str | None):
    from tracelayer.evidence.models import TestOutcome

    return TestOutcome(framework_id=framework_id, outcome=outcome, test_uid=test_uid)


def _exec_edge(run_id, test_uid, impl_uid, kind, hit_count):
    from tracelayer.evidence.models import ExecutionRecord

    return ExecutionRecord(
        run_id=run_id,
        test_uid=test_uid,
        implementation_uid=impl_uid,
        coverage_kind=kind,
        hit_count=hit_count,
    )


def req_impl_test_graph(store, *, test_trace_id="TEST:1", test_active=True):
    """REQ:1 <-verifies- test ; test -exercises-> IMPL:1 (work ancestry
    present); REQ:1 -addresses-> test so the deleted test has an incoming
    semantic edge (TL030 trigger)."""
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", start=10, end=40)
    test = make_node(test_trace_id, "test", path="tests/test_app.py", active=test_active)
    store.replace_all(
        [req, impl, test],
        [
            make_edge(impl.entity_uid, "work", req.entity_uid),
            make_edge(req.entity_uid, "addresses", test.entity_uid),
            make_edge(test.entity_uid, "verifies", req.entity_uid),
            make_edge(test.entity_uid, "exercises", impl.entity_uid),
        ],
    )
    return impl, test


def _strict_merge_project(project):
    """strict profile at merge: verification + test-pass + evidence gates on."""
    project.policy.profile = "strict"
    return project


# --------------------------------------------------------------------------
# Deleted test trace
# --------------------------------------------------------------------------


def test_deleted_test_trace_blocks_when_edge_still_active(project, store):
    """A test node marked inactive with an active verifies edge blocks (TL030)."""
    _strict_merge_project(project)
    req_impl_test_graph(store, test_active=False)
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
    )
    rule_ids = [d.rule_id for d in result.diagnostics]
    assert "TL030" in rule_ids
    assert result.blocking is True


def test_deleted_test_trace_clean_after_edges_retired(project, store):
    """Retiring the incoming verifies edge unblocks the deletion."""
    _strict_merge_project(project)
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", start=10, end=40)
    test = make_node("TEST:1", "test", path="tests/test_app.py", active=False)
    store.replace_all(
        [req, impl, test],
        [
            make_edge(impl.entity_uid, "work", req.entity_uid),
            make_edge(req.entity_uid, "addresses", test.entity_uid, status="retired"),
            make_edge(test.entity_uid, "verifies", req.entity_uid, status="retired"),
            make_edge(test.entity_uid, "exercises", impl.entity_uid),
        ],
    )
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    store.add_test_results(
        "run-1",
        [_outcome("tests.test_app.test_one", "pass", test.entity_uid)],
    )
    store.add_execution_edges(
        "run-1",
        [_exec_edge("run-1", "suite", impl.entity_uid, "suite", 3)],
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
        revision="abc123",
    )
    assert all(d.rule_id != "TL030" for d in result.diagnostics)


# --------------------------------------------------------------------------
# Renamed tests
# --------------------------------------------------------------------------


def test_renamed_test_keeps_identity_but_stale_evidence_fails_tl021(project, store):
    """Renaming a test (same trace id, new framework id) with evidence still
    under the old framework id cannot satisfy verification for the rename."""
    _strict_merge_project(project)
    impl, test = req_impl_test_graph(store)
    # the rename updates the test's framework id (spec FR-003 stability:
    # same trace identity); evidence collected under the OLD framework id
    # binds to the old test uid, which is no longer the verifying test.
    store.set_node_meta("TEST:1", "framework_test_id", "tests.app.test_renamed")
    junit = project.root / "junit.xml"
    junit.write_text(
        "<testsuite><testcase name='test_old' classname='tests.app'/></testsuite>",
        encoding="utf-8",
    )
    from tracelayer.evidence.ingest import ingest

    ingest(
        project,
        store,
        junit=junit,
        revision="abc123",
        test_id_map={"tests.app.test_old": "TEST:OLD"},
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
        revision="abc123",
    )
    # the ingested outcome is bound to TEST:OLD's uid; TEST:1 (the renamed,
    # verifying test) has no passing outcome -> TL021
    assert any(d.rule_id == "TL021" for d in result.diagnostics)


def test_renamed_test_evidence_under_new_framework_id_passes(project, store):
    _strict_merge_project(project)
    impl, test = req_impl_test_graph(store)
    store.set_node_meta("TEST:1", "framework_test_id", "tests.app.test_renamed")
    junit = project.root / "junit.xml"
    junit.write_text(
        "<testsuite><testcase name='test_renamed' classname='tests.app'/></testsuite>",
        encoding="utf-8",
    )
    from tracelayer.evidence.ingest import ingest

    ingest(
        project,
        store,
        junit=junit,
        revision="abc123",
        test_id_map={"tests.app.test_renamed": "TEST:1"},
    )
    store.add_execution_edges(
        "run-1",
        [_exec_edge("run-1", "suite", impl.entity_uid, "suite", 3)],
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
        revision="abc123",
    )
    assert all(d.rule_id != "TL021" for d in result.diagnostics)


# --------------------------------------------------------------------------
# Aggregate coverage cannot falsely claim L2
# --------------------------------------------------------------------------


def test_suite_coverage_cannot_falsely_claim_per_test_proof(project, store):
    """TL022 with preferred_coverage_proof=per_test fails even when suite
    coverage exists: aggregate evidence is L1, never L2."""
    _strict_merge_project(project)
    project.config.evidence.preferred_coverage_proof = "per_test"
    impl, test = req_impl_test_graph(store)
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    store.add_test_results(
        "run-1",
        [_outcome("tests.app.test_one", "pass", test.entity_uid)],
    )
    store.add_execution_edges(
        "run-1",
        [_exec_edge("run-1", "suite", impl.entity_uid, "suite", 9)],
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
        revision="abc123",
    )
    tl022 = [d for d in result.diagnostics if d.rule_id == "TL022"]
    assert len(tl022) == 1
    assert "proof level 1 < 2" in tl022[0].message
    assert result.blocking is True


def test_per_test_evidence_satisfies_l2_requirement(project, store):
    """A per_test execution edge for the exact test reaches L2."""
    _strict_merge_project(project)
    project.config.evidence.preferred_coverage_proof = "per_test"
    impl, test = req_impl_test_graph(store)
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    store.add_test_results(
        "run-1",
        [_outcome("tests.app.test_one", "pass", test.entity_uid)],
    )
    store.add_execution_edges(
        "run-1",
        [_exec_edge("run-1", test.entity_uid, impl.entity_uid, "per_test", 9)],
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"TEST:1", "IMPL:1", "REQ:1"},
        revision="abc123",
    )
    assert all(d.rule_id != "TL022" for d in result.diagnostics)
