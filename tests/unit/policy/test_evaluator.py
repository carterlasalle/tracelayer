"""evaluate() integration semantics (contract §P).

Whole-repo vs changed scope, requirement gating, waiver downgrade, expired
waivers (TL061), and blocking flag semantics — exercised through the full
evaluator against a fresh store.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta

from tests.unit.conftest import make_edge, make_node
from tracelayer.config import RequirementsConfig, Waiver
from tracelayer.diagnostics import SEVERITY_ERROR, SEVERITY_INFO
from tracelayer.policy.evaluator import evaluate


def make_git_repo(tmp_path) -> str:
    """Init a git repo in tmp_path; returns the HEAD revision."""
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "README.md").write_text("trace\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def req_impl_graph(store, *, with_ancestry: bool):
    """REQ:1 <-verifies- TEST:1 ; IMPL:1 (work-> REQ:1 when with_ancestry)."""
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", start=10, end=40)
    test = make_node("TEST:1", "test", path="tests/test_app.py")
    edges = [make_edge(test.entity_uid, "verifies", req.entity_uid)]
    if with_ancestry:
        edges.append(make_edge(impl.entity_uid, "work", req.entity_uid))
    edges.append(make_edge(test.entity_uid, "exercises", impl.entity_uid))
    store.replace_all([req, impl, test], edges)
    return req, impl, test


# --------------------------------------------------------------------------
# Requirement gating + blocking semantics
# --------------------------------------------------------------------------


def test_standard_merge_fails_changed_implementation_without_ancestry(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    result = evaluate(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    rule_ids = [d.rule_id for d in result.diagnostics]
    assert "TL010" in rule_ids
    assert result.blocking is True
    assert result.status == "fail"


def test_standard_merge_passes_with_requirement_ancestry(project, store):
    project.policy.profile = "standard"
    req, impl, test = req_impl_graph(store, with_ancestry=True)
    # evidence at the evaluated revision keeps TL021/TL022 green
    from tracelayer.evidence.models import ExecutionRecord, TestOutcome

    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    store.add_test_results(
        "run-1",
        [
            TestOutcome(
                framework_id="tests.test_app.test_one",
                outcome="pass",
                test_uid=test.entity_uid,
            )
        ],
    )
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1",
                test_uid="suite",
                implementation_uid=impl.entity_uid,
                coverage_kind="suite",
                hit_count=3,
            )
        ],
    )
    result = evaluate(
        project,
        store,
        lifecycle="merge",
        changed_ids={"IMPL:1", "REQ:1", "TEST:1"},
        revision="abc123",
    )
    assert result.blocking is False, [d.to_json() for d in result.diagnostics]
    assert result.status == "pass"


def test_wip_lifecycle_does_not_gate_verification_rules(project, store):
    """standard@wip has no requirements enabled, so a bare requirement passes."""
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    result = evaluate(project, store, lifecycle="wip", changed_ids={"IMPL:1", "REQ:1"})
    assert result.blocking is False


def test_requirement_gate_tl110_off_at_wip(project, store):
    project.policy.profile = "strict"
    stale = make_node("IMPL:1", "implementation", path="src/app.py", status="stale_review_required")
    store.replace_all([stale], [])
    result = evaluate(project, store, lifecycle="wip", changed_ids={"IMPL:1"})
    assert all(d.rule_id != "TL110" for d in result.diagnostics)


def test_requirement_gate_tl110_on_at_merge(project, store):
    project.policy.profile = "strict"
    stale = make_node("IMPL:1", "implementation", path="src/app.py", status="stale_review_required")
    store.replace_all([stale], [])
    result = evaluate(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    rule_ids = [d.rule_id for d in result.diagnostics]
    assert "TL110" in rule_ids
    assert result.blocking is True


def test_explicit_requirements_override_profile_defaults(project, store):
    """policy.requirements[wip] with require_work_ancestry=true gates TL010 on
    at a lifecycle where the profile defaults leave it off."""
    project.policy.profile = "strict"
    project.policy.requirements["wip"] = RequirementsConfig(require_work_ancestry=True)
    req_impl_graph(store, with_ancestry=False)
    result = evaluate(project, store, lifecycle="wip", changed_ids={"IMPL:1"})
    assert any(d.rule_id == "TL010" for d in result.diagnostics)
    assert result.blocking is True


# --------------------------------------------------------------------------
# Waiver semantics
# --------------------------------------------------------------------------


def test_active_waiver_downgrades_diagnostic_to_info(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    project.policy.waivers = [
        Waiver(rule="TL010", trace_id="IMPL:1", reason="deferred", owner="alice")
    ]
    result = evaluate(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    tl010 = [d for d in result.diagnostics if d.rule_id == "TL010"]
    assert len(tl010) == 1
    assert tl010[0].severity == SEVERITY_INFO
    assert tl010[0].metadata["waiver"] == "alice"
    # downgraded to INFO -> nothing blocks
    assert result.blocking is False
    assert result.status == "pass"


def test_waiver_requires_matching_trace_id(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    project.policy.waivers = [
        Waiver(rule="TL010", trace_id="SOME_OTHER_ID", reason="wrong target", owner="bob")
    ]
    result = evaluate(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    tl010 = [d for d in result.diagnostics if d.rule_id == "TL010"]
    assert len(tl010) == 1
    assert tl010[0].severity == SEVERITY_ERROR
    assert result.blocking is True


def test_waiver_downgrade_disabled_by_allow_waivers_false(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    project.policy.waivers = [
        Waiver(rule="TL010", trace_id="IMPL:1", reason="deferred", owner="alice")
    ]
    project.policy.requirements["merge"] = RequirementsConfig(allow_waivers=False)
    result = evaluate(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    tl010 = [d for d in result.diagnostics if d.rule_id == "TL010"]
    assert tl010[0].severity == SEVERITY_ERROR


def test_expired_waiver_emits_tl061_and_does_not_waive(project, store):
    project.policy.profile = "strict"
    req_impl_graph(store, with_ancestry=False)
    project.policy.waivers = [
        Waiver(
            rule="TL010",
            trace_id="IMPL:1",
            reason="deferred",
            expires=date.today() - timedelta(days=1),
            owner="alice",
        )
    ]
    result = evaluate(project, store, lifecycle="wip", changed_ids={"IMPL:1"})
    rule_ids = [d.rule_id for d in result.diagnostics]
    assert "TL061" in rule_ids
    # the expired waiver record itself blocks at strict
    assert result.blocking is True
    assert all(d.severity == SEVERITY_ERROR for d in result.diagnostics)


def test_tl061_cannot_waive_itself(project, store):
    project.policy.profile = "strict"
    project.policy.waivers = [
        Waiver(
            rule="TL061",
            reason="waive the waiver",
            expires=date.today() - timedelta(days=1),
            owner="alice",
        )
    ]
    store.replace_all([], [])
    result = evaluate(project, store, lifecycle="wip", changed_ids=set())
    tl061 = [d for d in result.diagnostics if d.rule_id == "TL061"]
    assert len(tl061) == 1
    assert tl061[0].severity == SEVERITY_ERROR
    assert "waiver" not in tl061[0].metadata


# --------------------------------------------------------------------------
# Scope: whole-repo vs changed
# --------------------------------------------------------------------------


def test_whole_repo_scope_checks_all_implementations(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    result = evaluate(project, store, lifecycle="merge")  # changed_ids=None
    assert any(d.rule_id == "TL010" for d in result.diagnostics)
    assert result.blocking is True


def test_changed_scope_ignores_unrelated_dirty_implementation(project, store):
    project.policy.profile = "standard"
    req_impl_graph(store, with_ancestry=False)
    result = evaluate(project, store, lifecycle="merge", changed_ids={"UNRELATED"})
    assert all(d.rule_id != "TL010" for d in result.diagnostics)
    # an empty scoped change over a clean node set passes
    assert result.blocking is False


def test_whole_repo_scope_with_changed_paths_for_tl012(project, store):
    """TL012 needs changed_paths; whole-repo scope passes it with none."""
    project.policy.profile = "strict"
    traced = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([traced], [])
    result = evaluate(project, store, lifecycle="wip")
    assert all(d.rule_id != "TL012" for d in result.diagnostics)


# --------------------------------------------------------------------------
# TL050 — evidence revision binding
# --------------------------------------------------------------------------


def test_tl050_blocks_when_evidence_bound_to_wrong_revision(project, store):
    project.policy.profile = "safety-critical"
    store.add_evidence_run("run-1", "old-rev", "pytest", None, None, None, "pass", None, {})
    result = evaluate(project, store, lifecycle="wip", revision="new-rev", changed_ids=set())
    assert any(d.rule_id == "TL050" for d in result.diagnostics)
    assert result.blocking is True


def test_tl050_clean_when_evidence_matches_evaluated_revision(project, store):
    project.policy.profile = "safety-critical"
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    result = evaluate(project, store, lifecycle="wip", revision="abc123", changed_ids=set())
    assert all(d.rule_id != "TL050" for d in result.diagnostics)
    assert result.blocking is False


def test_ingest_tl050_on_revision_mismatch_against_git_head(project, store, tmp_path):
    """Ingest against a real git repo: stale evidence revision raises TL050."""
    from tracelayer.evidence.ingest import ingest

    make_git_repo(tmp_path)
    project.root = tmp_path  # point the project at the git repo
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    junit = tmp_path / "junit.xml"
    junit.write_text(
        "<testsuite><testcase name='test_one' classname='tests.app'/></testsuite>",
        encoding="utf-8",
    )
    result = ingest(
        project,
        store,
        junit=junit,
        revision="deadbeef",
        test_id_map={"tests.app.test_one": "TEST:1"},
    )
    assert any(d.rule_id == "TL050" for d in result.diagnostics)
    assert result.tests_ingested == 1
    # the run is still recorded (bound to the stale revision) so the
    # evaluator can flag it too
    run = store.latest_evidence_run("deadbeef")
    assert run is not None


def test_ingest_missing_revision_emits_tl050_when_required(project, store):
    """require_revision=true (default) + no revision -> TL050 even without git."""
    from tracelayer.evidence.ingest import ingest

    junit = project.root / "junit.xml"
    junit.write_text(
        "<testsuite><testcase name='test_one' classname='tests.app'/></testsuite>",
        encoding="utf-8",
    )
    result = ingest(project, store, junit=junit, revision=None)
    assert any(d.rule_id == "TL050" for d in result.diagnostics)
    assert result.tests_ingested == 1


def test_ingest_no_tl050_when_require_revision_false(project, store):
    from tracelayer.evidence.ingest import ingest

    project.config.evidence.require_revision = False
    junit = project.root / "junit.xml"
    junit.write_text(
        "<testsuite><testcase name='test_one' classname='tests.app'/></testsuite>",
        encoding="utf-8",
    )
    result = ingest(project, store, junit=junit, revision=None)
    assert result.diagnostics == []
    assert store.latest_evidence_run() is not None


def test_diagnostics_are_sorted_deterministically(project, store):
    project.policy.profile = "standard"
    for i in range(3):
        impl = make_node(f"IMPL:{i}", "implementation", path=f"src/app{i}.py")
        store.replace_all([impl], [])
    result = evaluate(project, store, lifecycle="merge")
    keys = [(d.rule_id, d.trace_id or "", d.path or "") for d in result.diagnostics]
    assert keys == sorted(keys)


def test_unknown_profile_reports_tl100_and_degrades(project, store):
    project.policy.profile = "totally-bogus"
    store.replace_all([], [])
    result = evaluate(project, store, lifecycle="wip", changed_ids=set())
    assert any(d.rule_id == "TL100" for d in result.diagnostics)
    assert result.blocking is True
