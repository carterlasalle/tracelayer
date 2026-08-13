"""PreToolUse Write/Edit hook — block-once mutation gate (spec 22.3, FR-025).

The first edit of protected traced behavior is blocked when the session has
not loaded trace context for it; after `trace context <id>` runs (recording
the load) or after the one-time block, edits are allowed again.
"""

from __future__ import annotations

from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    edge_target_ids,
    fit,
    is_protected,
    linked_test_ids,
    node_at_path,
    render_allowed,
    render_blocked,
    sanitize_text,
)


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Block the first context-free edit of protected behavior (once)."""
    if ctx.store is None or ctx.state is None:
        return render_allowed("", {"event": "pre_mutation", "decision": "allow"})
    path = str(payload.get("path", ""))
    line = _as_int(payload.get("line"))
    cfg = ctx.project.config.hooks
    node = node_at_path(ctx.store, path, line)
    if node is None or not is_protected(ctx.store, node):
        return render_allowed("", {"event": "pre_mutation", "decision": "allow", "path": path})
    if not (cfg.pre_edit_require_context and cfg.pre_edit_block_once):
        return render_allowed("", {"event": "pre_mutation", "decision": "allow", "path": path})
    if ctx.state.context_loaded(ctx.session_id, node.trace_id):
        return render_allowed("", {"event": "pre_mutation", "decision": "allow", "path": path})
    if ctx.state.blocked_without_context(ctx.session_id, node.trace_id):
        return render_allowed("", {"event": "pre_mutation", "decision": "allow", "path": path})
    ctx.state.record_blocked_edit(ctx.session_id, node.trace_id)
    text = _block_text(ctx, node)
    return render_blocked(
        text,
        {
            "event": "pre_mutation",
            "decision": "block",
            "path": path,
            "line": line,
            "trace_id": node.trace_id,
            "output": text,
        },
    )


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _block_text(ctx: HookContext, node) -> str:
    """Render the spec 22.3 denial: identity, links, tests, retry steps."""
    store = ctx.store
    satisfied = edge_target_ids(store, node.entity_uid, ("satisfies",))
    work = edge_target_ids(store, node.entity_uid, ("work",))
    decisions = edge_target_ids(store, node.entity_uid, ("implements", "addresses"))
    tests = linked_test_ids(store, node, satisfied)
    lines = ["TRACE CONTEXT REQUIRED", "", "You are modifying:", f"  {node.trace_id}"]
    label = node.symbol_qualified_name or node.title or None
    if label:
        lines.append(f"  {sanitize_text(label, 160)}")
    for heading, ids in (("Satisfies", satisfied), ("Work", work), ("Decision", decisions)):
        if ids:
            lines += ["", f"{heading}:"] + [f"  {i}" for i in ids[:6]]
    if tests:
        lines += ["", "Linked verification:"] + [f"  {t}" for t in tests[:8]]
    target = satisfied[0] if satisfied else "the requirement"
    lines += [
        "",
        "Before editing:",
        f"1. Run `trace context {node.trace_id}`.",
        f"2. Confirm the intended behavior still satisfies {target}.",
        "3. Preserve the stable trace ID through refactors.",
        "4. Re-run linked verification after editing.",
        "",
        "Then retry the edit.",
    ]
    return fit("\n".join(lines), ctx.project.config.hooks.max_context_chars)
