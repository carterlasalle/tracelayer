"""Built-in policy profiles and their default requirements (spec 24.3).

A profile is a named rule vocabulary; a lifecycle tunes how strictly the
rules apply.  Rules with a requirement gate (see ``evaluator.REQUIREMENT_GATES``)
fire only when the effective requirements enable that gate, so e.g. TL020 only
blocks once ``require_verifying_test`` is true (standard turns it on at merge).
"""

from __future__ import annotations

from tracelayer.config import RequirementsConfig

PROFILES: list[str] = ["minimal", "standard", "strict", "safety-critical"]
LIFECYCLES: list[str] = ["draft", "wip", "review", "merge", "release"]

_MERGE_INDEX = LIFECYCLES.index("merge")

# Rule vocabulary per profile (spec 24.3).  minimal: marker hygiene plus
# stored parse diagnostics.  standard adds semantic-chain and verification
# rules plus config errors.  strict adds tracing-of-changed-behavior,
# execution-evidence, deletion and expired-waiver rules.  safety-critical
# adds evidence-revision and audit rules.
_BASE_RULES: dict[str, set[str]] = {
    "minimal": {
        "TL001",
        "TL002",
        "TL003",
        "TL004",
        "TL005",
        "TL006",
        "TL007",
        "TL040",
    },
    "standard": {
        "TL001",
        "TL002",
        "TL003",
        "TL004",
        "TL005",
        "TL006",
        "TL007",
        "TL040",
        "TL010",
        "TL011",
        "TL012",
        "TL013",
        "TL014",
        "TL020",
        "TL021",
        "TL070",
        "TL100",
    },
    "strict": {
        "TL001",
        "TL002",
        "TL003",
        "TL004",
        "TL005",
        "TL006",
        "TL007",
        "TL040",
        "TL010",
        "TL011",
        "TL020",
        "TL021",
        "TL100",
        "TL012",
        "TL013",
        "TL014",
        "TL022",
        "TL030",
        "TL061",
        "TL070",
    },
    "safety-critical": {
        "TL001",
        "TL002",
        "TL003",
        "TL004",
        "TL005",
        "TL006",
        "TL007",
        "TL040",
        "TL010",
        "TL011",
        "TL020",
        "TL021",
        "TL100",
        "TL012",
        "TL013",
        "TL014",
        "TL022",
        "TL030",
        "TL061",
        "TL050",
        "TL060",
        "TL062",
        "TL070",
    },
}


def _merge_plus(lifecycle: str) -> bool:
    """True when the lifecycle is merge or later (spec 24.3, TL110 gate).

    An unknown lifecycle degrades to ``False`` (no merge-only rules), which
    is the simplest deterministic behavior.
    """
    if lifecycle not in LIFECYCLES:
        return False
    return LIFECYCLES.index(lifecycle) >= _MERGE_INDEX


# trace:v1 id=impl.policy.profile-rules work=WORK-TL-001
def profile_rules(profile: str, lifecycle: str) -> set[str]:
    """Default enabled rule set for a profile at a lifecycle.

    TL110 joins every profile except minimal once the lifecycle reaches merge
    or release.  Unknown profiles degrade to the minimal set (the evaluator
    additionally reports them via TL100).
    """
    if profile not in _BASE_RULES:
        return set(_BASE_RULES["minimal"])
    rules = set(_BASE_RULES[profile])
    if profile != "minimal" and _merge_plus(lifecycle):
        rules.add("TL110")
    return rules


def profile_default_requirements(profile: str, lifecycle: str) -> RequirementsConfig:
    """Default requirement gates for a profile at a lifecycle (spec 24.3).

    - minimal: all False (nothing gated, at any lifecycle).
    - standard merge/release: work ancestry, requirement propagation,
      verifying test, test pass, block stale.
    - strict adds coverage confirmation and execution evidence at merge+,
      and semantic audit at release.
    - safety-critical: execution evidence and audit records at every
      lifecycle, strict's merge gates, plus semantic audit at release.

    Unknown profiles and lifecycles yield the minimal defaults; explicit
    policy config overrides these via ``evaluator.effective_requirements``.
    """
    reqs = RequirementsConfig()
    merge_plus = _merge_plus(lifecycle)
    if profile == "standard":
        if merge_plus:
            reqs.require_work_ancestry = True
            reqs.require_requirement_for_changed_behavior = True
            reqs.require_verifying_test = True
            reqs.require_test_pass = True
            reqs.block_stale = True
    elif profile == "strict":
        if merge_plus:
            reqs.require_work_ancestry = True
            reqs.require_requirement_for_changed_behavior = True
            reqs.require_verifying_test = True
            reqs.require_test_pass = True
            reqs.require_coverage_confirmation = True
            reqs.require_execution_evidence = True
            reqs.block_stale = True
        if lifecycle == "release":
            reqs.require_semantic_audit = True
    elif profile == "safety-critical":
        reqs.require_execution_evidence = True
        reqs.require_audit_records = True
        if merge_plus:
            reqs.require_work_ancestry = True
            reqs.require_requirement_for_changed_behavior = True
            reqs.require_verifying_test = True
            reqs.require_test_pass = True
            reqs.require_coverage_confirmation = True
            reqs.block_stale = True
        if lifecycle == "release":
            reqs.require_semantic_audit = True
    return reqs
