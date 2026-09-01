"""Individual policy rule functions against minimal store fixtures.

Rules are invoked directly via ``RULE_FUNCTIONS`` with a minimal
``EvalContext`` (contract §P): live rules inspect the graph store; re-emit
rules (TL001, TL040) surface diagnostics stored at index time.
"""

from __future__ import annotations

import pytest

from tests.unit.conftest import make_edge, make_node
from tracelayer.config import Project, Waiver
from tracelayer.diagnostics import make
from tracelayer.evidence.models import ExecutionRecord
from tracelayer.graph.models import Node
from tracelayer.policy.models import EvalContext
from tracelayer.policy.rules import RULE_FUNCTIONS


# trace:v1 id=test.dogfood.tests.unit.policy.test_rules.py type=test
def ctx_for(
    project: Project,
    store,
    *,
    lifecycle="wip",
    changed_ids=None,
    changed_paths=None,
    revision=None,
    audit_result=None,
) -> EvalContext:
    return EvalContext(
        project=project,
        store=store,
        lifecycle=lifecycle,
        changed_ids=changed_ids,
        changed_paths=set(changed_paths or ()),
        revision=revision,
        audit_result=audit_result,
    )


def rule(rule_id: str, c: EvalContext):
    return RULE_FUNCTIONS[rule_id](c)


def uids(*nodes: Node) -> list[str]:
    return [n.entity_uid for n in nodes]


# --------------------------------------------------------------------------
# Re-emit rules (parse-time diagnostics surfaced from the store)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", ["TL001", "TL040"])
def test_reemit_rule_surfaces_stored_diagnostics(project, store, rule_id: str):
    store.insert_diagnostics([make(rule_id, trace_id="REQ:1")])
    diags = rule(rule_id, ctx_for(project, store))
    assert [d.rule_id for d in diags] == [rule_id]
    assert diags[0].trace_id == "REQ:1"


def test_reemit_rule_absent_without_stored_diagnostics(project, store):
    assert rule("TL001", ctx_for(project, store)) == []


# --------------------------------------------------------------------------
# TL002 — edge target missing from the store
# --------------------------------------------------------------------------


def test_tl002_flags_edge_targeting_missing_node(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([req, impl], [make_edge(impl.entity_uid, "satisfies", "n_missing_xyz")])
    diags = rule("TL002", ctx_for(project, store))
    assert [d.rule_id for d in diags] == ["TL002"]
    assert diags[0].trace_id == "IMPL:1"
    assert "missing node" in diags[0].message


def test_tl002_ignores_inactive_edges(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all(
        [req, impl],
        [make_edge(impl.entity_uid, "satisfies", "n_missing_xyz", status="retired")],
    )
    assert rule("TL002", ctx_for(project, store)) == []


def test_tl002_clean_when_all_targets_resolve(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([req, impl], [make_edge(impl.entity_uid, "satisfies", req.entity_uid)])
    assert rule("TL002", ctx_for(project, store)) == []


# --------------------------------------------------------------------------
# TL003 — detached/ambiguous markers
# --------------------------------------------------------------------------


def test_tl003_flags_detached_metadata_flag(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py", metadata={"detached": True})
    store.replace_all([impl], [])
    diags = rule("TL003", ctx_for(project, store))
    assert [d.rule_id for d in diags] == ["TL003"]
    assert diags[0].trace_id == "IMPL:1"


def test_tl003_flags_ambiguous_structural_attachment_in_supported_language(project, store):
    impl = make_node(
        "IMPL:1",
        "implementation",
        path="src/app.py",
        metadata={"structural_attachment": "ambiguous", "parser_support": "python"},
    )
    store.replace_all([impl], [])
    diags = rule("TL003", ctx_for(project, store))
    assert [d.rule_id for d in diags] == ["TL003"]


def test_tl003_ok_for_generic_file_level_attachment(project, store):
    impl = make_node(
        "IMPL:1",
        "implementation",
        path="src/app.py",
        metadata={"structural_attachment": "file", "parser_support": "generic"},
    )
    store.replace_all([impl], [])
    assert rule("TL003", ctx_for(project, store)) == []


# --------------------------------------------------------------------------
# TL010 — changed implementation lacking requirement ancestry
# --------------------------------------------------------------------------


def test_tl010_clean_when_implementation_has_work_edge(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([req, impl], [make_edge(impl.entity_uid, "work", req.entity_uid)])
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    assert rule("TL010", c) == []


def test_tl010_clean_with_satisfies_edge(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([req, impl], [make_edge(impl.entity_uid, "satisfies", req.entity_uid)])
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    assert rule("TL010", c) == []


def test_tl010_flags_changed_implementation_without_ancestry(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([impl], [])
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    diags = rule("TL010", c)
    assert [d.rule_id for d in diags] == ["TL010"]
    assert diags[0].trace_id == "IMPL:1"
    assert "work=" in diags[0].message and "satisfies=" in diags[0].message


def test_tl010_whole_repo_scope_checks_every_implementation(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([impl], [])
    diags = rule("TL010", ctx_for(project, store))  # changed_ids=None
    assert [d.rule_id for d in diags] == ["TL010"]


def test_tl010_ignores_out_of_scope_nodes(project, store):
    """A dirty implementation outside the changed set is not flagged."""
    dirty = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([dirty], [])
    c = ctx_for(project, store, changed_ids={"SOMETHING_ELSE"})
    assert rule("TL010", c) == []


# --------------------------------------------------------------------------
# TL011 — changed requirement with a stale downstream node
# --------------------------------------------------------------------------


def _stale_downstream_graph(store, *, downstream_status: str):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", status=downstream_status)
    store.replace_all([req, impl], [make_edge(impl.entity_uid, "satisfies", req.entity_uid)])
    return req


def test_tl011_flags_stale_downstream_implementation(project, store):
    _stale_downstream_graph(store, downstream_status="stale_review_required")
    c = ctx_for(project, store, changed_ids={"REQ:1"})
    diags = rule("TL011", c)
    assert [d.rule_id for d in diags] == ["TL011"]
    assert diags[0].trace_id == "REQ:1"
    assert "IMPL:1" in diags[0].message


def test_tl011_clean_when_downstream_is_current(project, store):
    _stale_downstream_graph(store, downstream_status="current")
    c = ctx_for(project, store, changed_ids={"REQ:1"})
    assert rule("TL011", c) == []


def test_tl011_clean_when_requirement_not_in_changed_set(project, store):
    _stale_downstream_graph(store, downstream_status="stale_review_required")
    c = ctx_for(project, store, changed_ids={"OTHER"})
    assert rule("TL011", c) == []


# --------------------------------------------------------------------------
# TL012 — changed path with no traced behavior (strict)
# --------------------------------------------------------------------------


def test_tl012_flags_untraced_changed_path(project, store):
    (project.root / "src").mkdir(exist_ok=True)
    (project.root / "src" / "untraced.py").write_text("def f():\n    pass\n")
    traced = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([traced], [])
    c = ctx_for(project, store, changed_paths={"src/untraced.py"})
    diags = rule("TL012", c)
    assert [d.rule_id for d in diags] == ["TL012"]
    assert diags[0].path == "src/untraced.py"


def test_tl012_binary_changed_file_does_not_crash(project, store):
    """Regression: a binary changed file (e.g. an SCC SQLite state DB)
    must not crash the gate. Before the fix, _file_level_exempt's
    read_text raised UnicodeDecodeError (a ValueError, not OSError) and
    verify aborted; the gate must skip non-UTF-8 files instead.
    """
    (project.root / "state").mkdir(exist_ok=True)
    # 0xfb is an invalid UTF-8 start byte at a fixed early offset, the
    # same failure signature as a real SQLite database header.
    (project.root / "state" / "scc.db").write_bytes(b"SQLite format 3\x01" + b"\xfb" * 64)
    c = ctx_for(project, store, changed_paths={"state/scc.db"})
    diags = rule("TL012", c)
    # The path is still untraced (a binary file can host no marker), but
    # the rule must COMPLETE and flag it, not crash.
    assert [d.rule_id for d in diags] == ["TL012"]
    assert diags[0].path == "state/scc.db"


def test_tl012_clean_when_path_is_traced(project, store):
    traced = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([traced], [])
    c = ctx_for(project, store, changed_paths={"src/app.py"})
    assert rule("TL012", c) == []


def test_tl012_respects_policy_exclusion_globs(project, store):
    project.policy.exclusions.paths = ["vendor/**", "generated/**"]
    c = ctx_for(project, store, changed_paths={"vendor/lib.c", "generated/x.py"})
    assert rule("TL012", c) == []


def test_tl012_whole_repo_scope_emits_nothing(project, store):
    c = ctx_for(project, store)  # no changed paths
    assert rule("TL012", c) == []


def test_tl012_skips_deleted_paths(project, store):
    """F3: a deleted file can host no marker — TL012 must not demand one.

    Deletion hygiene is TL030's contract (inactive nodes with dangling
    incoming edges); TL012's remediation is impossible on an absent path.
    """
    (project.root / "src").mkdir(exist_ok=True)
    (project.root / "src" / "app.py").write_text("def f():\n    pass\n")
    traced = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([traced], [])
    # the file is deleted from the working tree; only the path remains
    (project.root / "src" / "app.py").unlink()
    c = ctx_for(project, store, changed_paths={"src/app.py"})
    assert rule("TL012", c) == []


def test_tl012_flags_untraced_path_that_exists(project, store):
    """F3 companion: an existing untraced path still gets TL012 (guard is
    existence-scoped, not a blanket skip)."""
    (project.root / "src").mkdir(exist_ok=True)
    (project.root / "src" / "untraced.py").write_text("def f():\n    pass\n")
    traced = make_node("IMPL:1", "implementation", path="src/app.py")
    store.replace_all([traced], [])
    c = ctx_for(project, store, changed_paths={"src/untraced.py"})
    diags = rule("TL012", c)
    assert [d.rule_id for d in diags] == ["TL012"]
    assert diags[0].path == "src/untraced.py"


# --------------------------------------------------------------------------
# TL020 — requirement without an incoming verifies edge
# --------------------------------------------------------------------------


def test_tl020_flags_requirement_without_verifies_edge(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    store.replace_all([req], [])
    c = ctx_for(project, store, changed_ids={"REQ:1"})
    diags = rule("TL020", c)
    assert [d.rule_id for d in diags] == ["TL020"]
    assert diags[0].trace_id == "REQ:1"


def test_tl020_clean_when_requirement_has_verifies_edge(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    test = make_node("TEST:1", "test", path="tests/test_app.py")
    store.replace_all([req, test], [make_edge(test.entity_uid, "verifies", req.entity_uid)])
    c = ctx_for(project, store, changed_ids={"REQ:1"})
    assert rule("TL020", c) == []


def test_tl020_ignores_non_requirement_node_types(project, store):
    doc = make_node("DOC:1", "doc", path="docs/guide.md")
    store.replace_all([doc], [])
    c = ctx_for(project, store, changed_ids={"DOC:1"})
    assert rule("TL020", c) == []


# --------------------------------------------------------------------------
# TL021 — linked test's latest outcome is not pass or missing
# --------------------------------------------------------------------------


def _req_with_test(store, *, outcome, revision="abc123", framework_id=None, bind_uid=True):
    from tracelayer.evidence.models import TestOutcome

    req = make_node("REQ:1", "requirement", path="docs/req.md")
    test = make_node("TEST:1", "test", path="tests/test_app.py", framework_test_id=framework_id)
    store.replace_all([req, test], [make_edge(test.entity_uid, "verifies", req.entity_uid)])
    outcomes = [
        TestOutcome(
            framework_id=framework_id or "tests.test_app.test_one",
            outcome=outcome,
            test_uid=test.entity_uid if bind_uid else None,
        )
    ]
    store.add_evidence_run("run-1", revision, "pytest", None, None, None, "pass", None, {})
    store.add_test_results("run-1", outcomes)


def test_tl021_clean_when_linked_test_passed(project, store):
    _req_with_test(store, outcome="pass")
    c = ctx_for(project, store, lifecycle="merge", changed_ids={"REQ:1"}, revision="abc123")
    assert rule("TL021", c) == []


def test_tl021_flags_failed_linked_test(project, store):
    _req_with_test(store, outcome="fail")
    c = ctx_for(project, store, lifecycle="merge", changed_ids={"REQ:1"}, revision="abc123")
    diags = rule("TL021", c)
    assert [d.rule_id for d in diags] == ["TL021"]
    assert diags[0].trace_id == "TEST:1"


def test_tl021_flags_missing_outcome(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    test = make_node("TEST:1", "test", path="tests/test_app.py")
    store.replace_all([req, test], [make_edge(test.entity_uid, "verifies", req.entity_uid)])
    c = ctx_for(project, store, changed_ids={"REQ:1"})
    diags = rule("TL021", c)
    assert [d.rule_id for d in diags] == ["TL021"]


# --------------------------------------------------------------------------
# TL022 — exercises edge without required execution evidence
# --------------------------------------------------------------------------


def _req_impl_test(store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", start=10, end=40)
    test = make_node("TEST:1", "test", path="tests/test_app.py")
    store.replace_all(
        [req, impl, test],
        [
            make_edge(test.entity_uid, "verifies", req.entity_uid),
            make_edge(test.entity_uid, "exercises", impl.entity_uid),
        ],
    )
    return impl


def test_tl022_flags_declared_only_exercises_edge(project, store):
    _req_impl_test(store)
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    diags = rule("TL022", c)
    assert [d.rule_id for d in diags] == ["TL022"]
    assert diags[0].trace_id == "IMPL:1"
    assert "proof level 0" in diags[0].message


def test_tl022_satisfied_by_suite_level_edge(project, store):
    impl = _req_impl_test(store)
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1",
                test_uid="suite",
                implementation_uid=impl.entity_uid,
                coverage_kind="suite",
                hit_count=5,
            )
        ],
    )
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    assert rule("TL022", c) == []


def test_tl022_per_test_requirement_not_satisfied_by_suite_edge(project, store):
    """Aggregate coverage cannot satisfy a per_test proof requirement."""
    project.config.evidence.preferred_coverage_proof = "per_test"
    impl = _req_impl_test(store)
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1",
                test_uid="suite",
                implementation_uid=impl.entity_uid,
                coverage_kind="suite",
                hit_count=5,
            )
        ],
    )
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    diags = rule("TL022", c)
    assert [d.rule_id for d in diags] == ["TL022"]
    assert "proof level 1 < 2" in diags[0].message


def test_tl022_out_of_scope_implementation_not_checked(project, store):
    _req_impl_test(store)
    c = ctx_for(project, store, changed_ids={"OTHER"})
    assert rule("TL022", c) == []


# --------------------------------------------------------------------------
# TL030 — inactive node with active incoming semantic edge
# --------------------------------------------------------------------------


def test_tl030_flags_inactive_node_with_active_incoming_edge(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", active=False)
    store.replace_all([req, impl], [make_edge(req.entity_uid, "addresses", impl.entity_uid)])
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    diags = rule("TL030", c)
    assert [d.rule_id for d in diags] == ["TL030"]
    assert diags[0].trace_id == "IMPL:1"


def test_tl030_ignores_observed_edges(project, store):
    """Historical observed edges (executed/passed) never block deletion."""
    test = make_node("TEST:1", "test", path="tests/test_app.py")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", active=False)
    store.replace_all(
        [test, impl],
        [make_edge(test.entity_uid, "executed", impl.entity_uid)],
    )
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    assert rule("TL030", c) == []


def test_tl030_clean_for_active_node(project, store):
    req = make_node("REQ:1", "requirement", path="docs/req.md")
    impl = make_node("IMPL:1", "implementation", path="src/app.py", active=True)
    store.replace_all([req, impl], [make_edge(req.entity_uid, "addresses", impl.entity_uid)])
    c = ctx_for(project, store, changed_ids={"IMPL:1"})
    assert rule("TL030", c) == []


# --------------------------------------------------------------------------
# TL050 — evidence run bound to a different revision
# --------------------------------------------------------------------------


def test_tl050_flags_revision_mismatch(project, store):
    store.add_evidence_run("run-1", "rev-old", "pytest", None, None, None, "pass", None, {})
    c = ctx_for(project, store, revision="rev-new")
    diags = rule("TL050", c)
    assert [d.rule_id for d in diags] == ["TL050"]
    assert "rev-old" in diags[0].message and "rev-new" in diags[0].message


def test_tl050_clean_when_revision_matches(project, store):
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    c = ctx_for(project, store, revision="abc123")
    assert rule("TL050", c) == []


def test_tl050_silent_without_evaluated_revision(project, store):
    store.add_evidence_run("run-1", "abc123", "pytest", None, None, None, "pass", None, {})
    c = ctx_for(project, store, revision=None)
    assert rule("TL050", c) == []


# --------------------------------------------------------------------------
# TL061 — expired waivers
# --------------------------------------------------------------------------


def test_tl061_flags_expired_waiver(project, store):
    from datetime import date

    project.policy.waivers = [
        Waiver(rule="TL020", reason="pending", expires=date(2020, 1, 1), owner="alice")
    ]
    diags = rule("TL061", ctx_for(project, store))
    assert [d.rule_id for d in diags] == ["TL061"]
    assert diags[0].metadata["owner"] == "alice"
    assert "expired" in diags[0].message


def test_tl061_clean_with_active_waiver(project, store):
    from datetime import date, timedelta

    project.policy.waivers = [
        Waiver(
            rule="TL020",
            reason="pending",
            expires=date.today() + timedelta(days=30),
            owner="alice",
        )
    ]
    assert rule("TL061", ctx_for(project, store)) == []


def test_tl061_clean_without_policy(project, store):
    project.policy = None
    assert rule("TL061", ctx_for(project, store)) == []


# --------------------------------------------------------------------------
# TL110 — stale nodes block merge/release
# --------------------------------------------------------------------------


def test_tl110_flags_stale_node_at_merge(project, store):
    stale = make_node("IMPL:1", "implementation", path="src/app.py", status="stale_review_required")
    store.replace_all([stale], [])
    c = ctx_for(project, store, lifecycle="merge", changed_ids={"IMPL:1"})
    diags = rule("TL110", c)
    assert [d.rule_id for d in diags] == ["TL110"]
    assert diags[0].trace_id == "IMPL:1"


def test_tl110_silent_before_merge(project, store):
    stale = make_node("IMPL:1", "implementation", path="src/app.py", status="stale_review_required")
    store.replace_all([stale], [])
    c = ctx_for(project, store, lifecycle="wip", changed_ids={"IMPL:1"})
    assert rule("TL110", c) == []


def test_tl110_clean_for_current_nodes(project, store):
    current = make_node("IMPL:1", "implementation", path="src/app.py", status="current")
    store.replace_all([current], [])
    c = ctx_for(project, store, lifecycle="release", changed_ids={"IMPL:1"})
    assert rule("TL110", c) == []


def test_tl110_whole_repo_scope(project, store):
    stale = make_node("IMPL:1", "implementation", path="src/app.py", status="stale_review_required")
    store.replace_all([stale], [])
    c = ctx_for(project, store, lifecycle="merge")
    assert [d.rule_id for d in rule("TL110", c)] == ["TL110"]
