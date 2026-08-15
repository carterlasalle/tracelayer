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


# trace:v1 id=impl.hooks.stop-gate work=WORK-TL-001
def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Block completion on blocking verify failures; else confirm pass."""
    policy = ctx.project.policy
    lifecycle = payload.get("lifecycle") or (policy.lifecycle_for(None) if policy else "wip")
    pending = ctx.state.pending_obligations(ctx.session_id) if ctx.state else []
    result = _verify_both(ctx, lifecycle)
    json_data = {
        "event": "stop",
        "decision": "block" if (result["blocking"] or pending) else "allow",
        "status": result["status"],
        "lifecycle": lifecycle,
        "pending_obligations": pending,
        "diagnostics": [d.to_json() for d in result["diagnostics"]],
        "output": "",
    }
    if result.get("unavailable"):
        text = "Trace index unavailable. Run `trace index --all`, then retry."
        json_data["output"] = text
        return render_blocked(text, json_data)
    if pending:
        text = _obligation_text(pending)
        json_data["output"] = text
        return render_blocked(text, json_data)
    if result["blocking"]:
        text = _failure_text(ctx, result["diagnostics"])
        json_data["output"] = text
        return render_blocked(text, json_data)
    obligations = ctx.state._read(ctx.session_id).get("obligations", []) if ctx.state else []
    satisfied = sum(1 for o in obligations if o.get("state") == "satisfied")
    text = f"Trace verify passed under lifecycle {lifecycle}." + (
        f" Trace obligations: {satisfied} satisfied, {len(obligations) - satisfied} pending."
        if obligations
        else ""
    )
    finalize_note = _auto_finalize(ctx)
    if finalize_note:
        text = text + "\n" + finalize_note
    json_data["output"] = text
    return render_allowed(text, json_data)


# trace:exempt reason=internal-helper
def _auto_finalize(ctx: HookContext) -> str:
    """Safe finalizer: on a successful stop, attempt to finalize the active
    work under merge-grade policy (adversarial review P0: task completion
    must be automatic, and ``done`` must mean merge-grade verified).

    Failure is non-blocking here — the stop gate's own decision already
    passed at the session lifecycle — but the agent is told exactly which
    merge-grade items are still missing.
    """
    if ctx.state is None or ctx.store is None:
        return ""
    work = ctx.state.active_work(ctx.session_id)
    if not work:
        return ""
    try:
        from tracelayer.tasks import finish as ambient_finish

        result = ambient_finish(ctx.project, ctx.store, session_id=ctx.session_id)
    except Exception:
        return ""
    if result.get("status") == "done":
        return (
            f"Ambient: work {work} finalized (status=done, receipts bound to the current commit)."
        )
    diags = ", ".join(str(d) for d in (result.get("diagnostics") or [])[:6])
    return (
        f"Ambient: work {work} is NOT yet done — the merge-grade completion "
        f"gate requires more before finalization: {diags or 'pending obligations'}. "
        "Run linked tests, ingest evidence, resolve the diagnostics, then "
        "`trace task finish --auto`."
    )


def _verify_both(ctx: HookContext, lifecycle: str) -> dict:
    """Union changed-scope and whole-graph verification.

    ``scope='changed'`` reindexes incrementally and evaluates the change set
    (TL012 etc.); ``scope='all'`` evaluates global graph health. The gate
    must enforce both: a brand-new untraced file is a changed-scope failure
    even when the whole graph is healthy.
    """
    try:
        from tracelayer.engine import Engine

        engine = Engine(ctx.project, ctx.gitrepo)
        changed = engine.verify(scope="changed", lifecycle=lifecycle)
        whole = engine.verify(scope="all", lifecycle=lifecycle)
        diagnostics = list(changed.diagnostics) + list(whole.diagnostics)
        blocking = changed.blocking or whole.blocking
        return {
            "status": "fail" if blocking else "pass",
            "blocking": blocking,
            "diagnostics": diagnostics,
        }
    except (ImportError, AttributeError):
        pass  # engine not implemented yet (or contract drift) — direct fallback
    return _evaluate_fallback(ctx, lifecycle)


def _obligation_text(pending: list[dict]) -> str:
    lines = ["TRACE OBLIGATIONS PENDING", ""]
    for obl in pending[:8]:
        marker = str(obl.get("suggested_marker", "")).strip()
        lines.append(f"- {obl.get('path')}::{obl.get('symbol')} (new behavior)")
        if marker:
            lines.append(f"    add: {marker}")
    lines += ["", "Resolve every obligation before completing the task."]
    return "\n".join(lines)


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
