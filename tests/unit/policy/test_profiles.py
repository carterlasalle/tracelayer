"""Profile rule vocabularies and default requirements (contract §P, spec 24.3)."""

from __future__ import annotations

import pytest

from tracelayer.policy.profiles import (
    LIFECYCLES,
    PROFILES,
    profile_default_requirements,
    profile_rules,
)

# Base rule vocabulary shared by every profile (marker hygiene + stored parse
# diagnostics).  standard/strict/safety-critical add semantic + verification
# rules; TL012 (changed path must be traced) is standard-or-later; strict
# adds TL022/TL030/TL061; safety-critical adds TL050/TL060/TL062.
_BASE = {"TL001", "TL002", "TL003", "TL004", "TL005", "TL006", "TL007", "TL040"}
_SEMANTIC = {"TL010", "TL011", "TL020", "TL021", "TL100"}
_STANDARD_EXTRA = {"TL012", "TL013"}
_STRICT_EXTRA = {"TL022", "TL030", "TL061"}
_SC_EXTRA = {"TL050", "TL060", "TL062"}

EXPECTED_BASE = {
    "minimal": set(_BASE),
    "standard": _BASE | _SEMANTIC | _STANDARD_EXTRA,
    "strict": _BASE | _SEMANTIC | _STANDARD_EXTRA | _STRICT_EXTRA,
    "safety-critical": _BASE | _SEMANTIC | _STANDARD_EXTRA | _STRICT_EXTRA | _SC_EXTRA,
}

PRE_MERGE = ("draft", "wip", "review")
MERGE_PLUS = ("merge", "release")


def test_profiles_and_lifecycles_are_stable():
    assert PROFILES == ["minimal", "standard", "strict", "safety-critical"]
    assert LIFECYCLES == ["draft", "wip", "review", "merge", "release"]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("lifecycle", LIFECYCLES)
def test_profile_rules_base_vocabulary(profile: str, lifecycle: str):
    """Every profile keeps its base vocabulary at every lifecycle."""
    rules = profile_rules(profile, lifecycle)
    assert EXPECTED_BASE[profile] <= rules


@pytest.mark.parametrize("profile", ["standard", "strict", "safety-critical"])
@pytest.mark.parametrize("lifecycle", MERGE_PLUS)
def test_tl110_joins_non_minimal_profiles_at_merge_plus(profile: str, lifecycle: str):
    assert "TL110" in profile_rules(profile, lifecycle)


@pytest.mark.parametrize("profile", ["standard", "strict", "safety-critical"])
@pytest.mark.parametrize("lifecycle", PRE_MERGE)
def test_tl110_absent_before_merge(profile: str, lifecycle: str):
    assert "TL110" not in profile_rules(profile, lifecycle)


@pytest.mark.parametrize("lifecycle", LIFECYCLES)
def test_minimal_never_gains_tl110(lifecycle: str):
    assert "TL110" not in profile_rules("minimal", lifecycle)


def test_profile_rules_exact_sets():
    """Exact membership per profile/lifecycle (no stray rules)."""
    for profile in PROFILES:
        for lifecycle in LIFECYCLES:
            rules = profile_rules(profile, lifecycle)
            expected = set(EXPECTED_BASE[profile])
            if profile != "minimal" and lifecycle in MERGE_PLUS:
                expected.add("TL110")
            assert rules == expected, f"{profile}@{lifecycle}: {rules}"


def test_unknown_profile_degrades_to_minimal():
    assert profile_rules("bogus", "merge") == profile_rules("minimal", "merge")


def test_unknown_lifecycle_has_no_merge_rules():
    assert "TL110" not in profile_rules("standard", "deploy")
    assert profile_rules("standard", "deploy") == profile_rules("standard", "wip")


def test_minimal_default_requirements_all_false():
    for lifecycle in LIFECYCLES:
        reqs = profile_default_requirements("minimal", lifecycle)
        assert not any(
            [
                reqs.require_work_ancestry,
                reqs.require_requirement_for_changed_behavior,
                reqs.require_verifying_test,
                reqs.require_test_pass,
                reqs.require_coverage_confirmation,
                reqs.require_execution_evidence,
                reqs.block_stale,
                reqs.require_semantic_audit,
                reqs.require_audit_records,
            ]
        )


def test_standard_merge_enables_core_gates():
    reqs = profile_default_requirements("standard", "merge")
    assert reqs.require_work_ancestry
    assert reqs.require_requirement_for_changed_behavior
    assert reqs.require_verifying_test
    assert reqs.require_test_pass
    assert reqs.block_stale
    # evidence gates stay off for standard
    assert not reqs.require_coverage_confirmation
    assert not reqs.require_execution_evidence


def test_standard_wip_has_no_gates():
    reqs = profile_default_requirements("standard", "wip")
    assert not reqs.require_work_ancestry
    assert not reqs.require_test_pass
    assert not reqs.block_stale


def test_strict_merge_adds_evidence_gates():
    reqs = profile_default_requirements("strict", "merge")
    assert reqs.require_work_ancestry
    assert reqs.require_verifying_test
    assert reqs.require_test_pass
    assert reqs.require_coverage_confirmation
    assert reqs.require_execution_evidence
    assert reqs.block_stale
    assert not reqs.require_semantic_audit


def test_strict_release_adds_semantic_audit():
    reqs = profile_default_requirements("strict", "release")
    assert reqs.require_semantic_audit
    assert reqs.require_execution_evidence


def test_safety_critical_evidence_gates_at_every_lifecycle():
    for lifecycle in LIFECYCLES:
        reqs = profile_default_requirements("safety-critical", lifecycle)
        assert reqs.require_execution_evidence, lifecycle
        assert reqs.require_audit_records, lifecycle


def test_safety_critical_merge_adds_work_and_test_gates():
    reqs = profile_default_requirements("safety-critical", "merge")
    assert reqs.require_work_ancestry
    assert reqs.require_verifying_test
    assert reqs.require_test_pass
    assert reqs.require_coverage_confirmation
    assert reqs.block_stale


def test_safety_critical_release_semantic_audit():
    reqs = profile_default_requirements("safety-critical", "release")
    assert reqs.require_semantic_audit
