"""Policy evaluation entry point (spec Section 24, contract §P).

``evaluate`` assembles the enabled rule set from the profile vocabulary and
the effective requirements, runs the rules, applies waivers (downgrading
waived ERROR/WARNING diagnostics to INFO), and computes blocking from any
remaining ERROR diagnostic.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from tracelayer.config import PolicyConfig, RequirementsConfig, Waiver
from tracelayer.diagnostics import SEVERITY_INFO, Diagnostic, blocking, make
from tracelayer.policy.models import EvalContext, PolicyResult
from tracelayer.policy.profiles import PROFILES, profile_default_requirements, profile_rules
from tracelayer.policy.rules import RULE_FUNCTIONS

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from tracelayer.config import Project
    from tracelayer.graph.store import GraphStore

# Rules that fire only when the matching effective requirement is true.
# Profile vocabulary may list these (profiles.py) but membership alone does
# not enable them: e.g. TL020/TL021 are in the standard vocabulary, yet only
# block once require_verifying_test/require_test_pass are on (standard's
# defaults turn them on at merge/release).  This keeps `trace verify` at wip
# green before any evidence exists.
REQUIREMENT_GATES: dict[str, str] = {
    "TL010": "require_work_ancestry",
    "TL011": "require_requirement_for_changed_behavior",
    "TL020": "require_verifying_test",
    "TL021": "require_test_pass",
    "TL022": "require_execution_evidence",
    "TL060": "require_semantic_audit",
    "TL110": "block_stale",
}


def effective_requirements(
    policy: PolicyConfig | None, profile: str, lifecycle: str
) -> RequirementsConfig:
    """Explicit ``policy.requirements[lifecycle]`` merged over the profile
    defaults; explicitly set policy fields win.
    """
    base = profile_default_requirements(profile, lifecycle)
    if policy is None:
        return base
    override = policy.requirements.get(lifecycle)
    if override is None:
        return base
    return base.model_copy(
        update={name: getattr(override, name) for name in override.model_fields_set}
    )


def active_waivers(
    policy: PolicyConfig | None,
    rule_id: str,
    trace_id: str | None = None,
    path: str | None = None,
    *,
    include_expired: bool = False,
) -> list[Waiver]:
    """Non-expired waivers matching (rule, trace_id, path).

    Pass ``include_expired=True`` when the caller needs expired records too
    (TL061 reports them; they never waive a diagnostic).
    """
    if policy is None or not policy.waivers:
        return []
    return [
        w
        for w in policy.waivers
        if (not w.expired() or include_expired) and w.matches(rule_id, trace_id, path)
    ]


def _apply_waiver(d: Diagnostic, policy: PolicyConfig | None) -> Diagnostic:
    """Downgrade a diagnostic to INFO when an active waiver matches it.

    TL061 (expired waivers) cannot waive itself.  Waiving requires
    ``allow_waivers`` in the effective requirements, checked by the caller.
    """
    if d.rule_id == "TL061":
        return d
    waiver = active_waivers(policy, d.rule_id, d.trace_id, d.path)
    if not waiver:
        return d
    w = waiver[0]
    return dataclasses.replace(
        d,
        severity=SEVERITY_INFO,
        metadata={**d.metadata, "waiver": w.owner if w.owner is not None else w.reason},
    )


def evaluate(
    project: Project,
    store: GraphStore,
    *,
    lifecycle: str | None = None,
    changed_ids: set[str] | None = None,
    changed_paths: set[str] | None = None,
    revision: str | None = None,
    audit_result: dict | None = None,
) -> PolicyResult:
    """Evaluate policy for a project against the current graph store.

    ``lifecycle`` defaults through ``project.policy.lifecycle_for(None)``
    (falling back to "wip" when there is no policy file).  ``changed_ids``
    are trace IDs of the change under evaluation; ``None`` means whole-repo
    scope.  Rules in ``REQUIREMENT_GATES`` fire only when their effective
    requirement is enabled, regardless of profile vocabulary membership.
    Blocking is true when any diagnostic (after waiver downgrade) has ERROR
    severity.
    """
    policy = project.policy
    if lifecycle is None:
        lifecycle = policy.lifecycle_for(None) if policy is not None else "wip"
    profile = policy.profile if policy is not None else "standard"

    diags: list[Diagnostic] = []
    if profile not in PROFILES:
        diags.append(
            make(
                "TL100",
                lifecycle=lifecycle,
                message=(
                    f"Unknown policy profile {profile!r}; falling back to "
                    f"minimal rules"
                ),
            )
        )
        profile = "minimal"

    requirements = effective_requirements(policy, profile, lifecycle)
    profile_set = profile_rules(profile, lifecycle)
    enabled = {r for r in profile_set if r not in REQUIREMENT_GATES}
    enabled.update(
        rule_id
        for rule_id, gate in REQUIREMENT_GATES.items()
        if getattr(requirements, gate)
    )

    ctx = EvalContext(
        project=project,
        store=store,
        lifecycle=lifecycle,
        changed_ids=changed_ids,
        changed_paths=set(changed_paths or ()),
        revision=revision,
        audit_result=audit_result,
    )
    for rule_id in sorted(enabled):
        fn = RULE_FUNCTIONS.get(rule_id)
        if fn is not None:
            diags.extend(fn(ctx))

    if requirements.allow_waivers:
        diags = [_apply_waiver(d, policy) for d in diags]

    diags.sort(
        key=lambda d: (
            d.rule_id,
            d.trace_id or "",
            d.path or "",
            d.line if d.line is not None else -1,
            d.message,
        )
    )
    is_blocking = blocking(diags)
    return PolicyResult(
        status="fail" if is_blocking else "pass",
        blocking=is_blocking,
        diagnostics=diags,
    )
