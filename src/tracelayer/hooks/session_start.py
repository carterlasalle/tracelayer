"""SessionStart hook (spec 22.1, FR-024): compact health announcement."""

from __future__ import annotations

from tracelayer.diagnostics import Diagnostic
from tracelayer.hooks.common import HookContext, HookOutput, fit, render_allowed


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Render the session-start injection (<= 400 chars while healthy)."""
    broken: list[Diagnostic] = []
    stale: list[str] = []
    if ctx.store is not None:
        broken = ctx.store.get_diagnostics(rule_id="TL002")
        stale = sorted(n.trace_id for n in ctx.store.all_nodes() if n.status() != "current")
    policy = ctx.project.policy
    profile = policy.profile if policy else "standard"
    lifecycle = policy.lifecycle_for(None) if policy else "wip"
    lines = [
        "TraceLayer active.",
        f"Health: {len(broken)} broken refs, {len(stale)} stale traces.",
        f"Policy: {profile} ({lifecycle}).",
        "For traced behavior, load `trace context <id>` before mutation.",
        "`trace verify --changed` is required before completion.",
    ]
    if broken or stale:
        ids = stale[:5] + [d.trace_id for d in broken[:5] if d.trace_id]
        lines.append("Review: " + ", ".join(ids))
    cap = ctx.project.config.hooks.max_context_chars if (broken or stale) else 400
    text = fit("\n".join(lines), cap)
    return render_allowed(
        text,
        {
            "event": "session_start",
            "decision": "allow",
            "output": text,
            "health": {
                "broken_refs": len(broken),
                "stale": len(stale),
                "policy": profile,
                "lifecycle": lifecycle,
            },
        },
    )
