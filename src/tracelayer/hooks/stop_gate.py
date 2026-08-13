"""Stop hook (spec 22.9, FR-027): fail-closed gate on the full graph state.

Delegates to the trace engine's verify over the whole materialized store
(lazy import so hooks load before the engine lands); otherwise falls back
to a direct policy evaluation over the full store. Changed-scope-only checks
are insufficient because stale/blocking state (e.g. STALE_REVIEW_REQUIRED
nodes under block_stale) persists after commits and re-indexing — the gate
must block whenever the materialized graph holds blocking diagnostics at the
requested lifecycle, independent of the change set (P11). Only actionable
blocking failures are injected (spec 22.9).
"""

from __future__ import annotations

from tracelayer.diagnostics import SEVERITY_ERROR, Diagnostic
from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    fit,
    render_allowed,
    render_blocked,
    sanitize_text,
)


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Block completion on blocking verify failures; else confirm pass."""
    policy = ctx.project.policy
    lifecycle = payload.get("lifecycle") or (
        policy.lifecycle_for(None) if policy else "wip"
    )
    result = _verify_changed(ctx, lifecycle)
    json_data = {
        "event": "stop",
        "decision": "block" if result["blocking"] else "allow",
        "status": result["status"],
        "lifecycle": lifecycle,
        "diagnostics": [d.to_json() for d in result["diagnostics"]],
        "output": "",
    }
    if result.get("unavailable"):
        text = "Trace index unavailable. Run `trace index --all`, then retry."
        json_data["output"] = text
        return render_blocked(text, json_data)
    if result["blocking"]:
        text = _failure_text(ctx, result["diagnostics"])
        json_data["output"] = text
        return render_blocked(text, json_data)
    text = f"Trace verify passed under lifecycle {lifecycle}."
    json_data["output"] = text
    return render_allowed(text, json_data)


def _verify_changed(ctx: HookContext, lifecycle: str) -> dict:
    """Verify the full materialized graph state; prefer the engine, else evaluator.

    ``Engine.verify(scope="all")`` evaluates the existing store only (it does
    not reindex — only ``scope="changed"`` triggers an incremental rebuild), so
    it is safe to call here: the gate reflects every blocking diagnostic
    currently materialized at ``lifecycle``, not just the change set.
    """
    try:
        from tracelayer.engine import Engine

        result = Engine(ctx.project, ctx.gitrepo).verify(
            scope="all", lifecycle=lifecycle
        )
        return {
            "status": result.status,
            "blocking": result.blocking,
            "diagnostics": result.diagnostics,
        }
    except (ImportError, AttributeError):
        pass  # engine not implemented yet (or contract drift) — direct fallback
    return _evaluate_fallback(ctx, lifecycle)


def _evaluate_fallback(ctx: HookContext, lifecycle: str) -> dict:
    if ctx.store is None:
        return {
            "status": "fail",
            "blocking": True,
            "diagnostics": [],
            "unavailable": True,
        }
    from tracelayer.policy import evaluator

    revision = ctx.gitrepo.rev() if ctx.gitrepo is not None else None
    res = evaluator.evaluate(
        ctx.project,
        ctx.store,
        lifecycle=lifecycle,
        changed_ids=None,
        changed_paths=set(),
        revision=revision,
    )
    return {"status": res.status, "blocking": res.blocking, "diagnostics": res.diagnostics}


def _failure_text(ctx: HookContext, diagnostics: list[Diagnostic]) -> str:
    """Render only actionable blocking failures (spec 22.9)."""
    blocking = [d for d in diagnostics if d.severity == SEVERITY_ERROR]
    lines = ["Task cannot complete yet.", ""]
    for d in blocking[:10]:
        where = d.trace_id or d.path or ""
        lines.append(f"- [{d.rule_id}] {where}: {sanitize_text(d.message, 300)}")
        if d.remediation:
            lines.append(f"  {sanitize_text(d.remediation, 300)}")
    lines += ["", "Resolve the failures, then re-run `trace verify`."]
    return fit("\n".join(lines), ctx.project.config.hooks.max_context_chars)
