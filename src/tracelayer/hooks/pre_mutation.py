"""PreToolUse Write/Edit hook — authoring + context gate (spec 22.3, FR-025).

Two gates, both evaluated BEFORE the mutation lands:

1. **Authoring gate (P0)**: the proposed edit is simulated and parsed. New
   behavioral boundaries (functions/classes/methods) without a trace marker
   or an explicit ``# trace:exempt`` are BLOCKED with the exact marker to
   write — new code cannot exist before tracing is considered. The
   obligation is persisted in session state so the stop gate can enforce it.

2. **Context gate (spec 22.3)**: the first edit of protected traced
   behavior is blocked when the session has not loaded trace context for
   it; after ``trace context <id>`` runs (recording the load) or after the
   one-time block, edits are allowed again.
"""

from __future__ import annotations

from pathlib import Path

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


# trace:v1 id=impl.hooks.authoring-gate work=WORK-TL-001
def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Block untraced new behavior; then the context-free edit of protected nodes."""
    allow = {"event": "pre_mutation", "decision": "allow"}
    if ctx.store is None or ctx.state is None:
        return render_allowed("", allow)
    path = str(payload.get("path", ""))
    line = _as_int(payload.get("line"))
    authoring = _authoring_block(ctx, path, payload)
    if authoring is not None:
        return authoring
    cfg = ctx.project.config.hooks
    node = node_at_path(ctx.store, path, line)
    if node is None or not is_protected(ctx.store, node):
        return render_allowed("", {**allow, "path": path})
    if not (cfg.pre_edit_require_context and cfg.pre_edit_block_once):
        return render_allowed("", {**allow, "path": path})
    if ctx.state.context_loaded(ctx.session_id, node.trace_id):
        return render_allowed("", {**allow, "path": path})
    if ctx.state.blocked_without_context(ctx.session_id, node.trace_id):
        return render_allowed("", {**allow, "path": path})
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


# ---------------------------------------------------------------------------
# Authoring gate: proposed-edit classification (review P0)
# ---------------------------------------------------------------------------

_EXEMPT_MARK = "# trace:exempt"


def _proposed_text(root: Path, path: str, payload: dict) -> str | None:
    """Simulate the mutation: Write content, or Edit old->new replacement."""
    content = payload.get("content")
    if content is not None:
        return str(content)
    old = payload.get("old_string")
    new = payload.get("new_string")
    if old is None or new is None:
        return None  # no mutation text available; fall back to current file
    current = _read_file(root, path)
    if current is None:
        return None
    if old in current:
        return current.replace(old, str(new), 1)
    return None  # old_string mismatch; do not guess


def _read_file(root: Path, path: str) -> str | None:
    from tracelayer.hooks.post_mutation import _read_text

    return _read_text(root, path)


def _new_boundaries(root: Path, path: str, current: str | None, proposed: str) -> list:
    """Boundaries the proposed edit introduces (code, markdown, config)."""
    from tracelayer.discovery.boundaries import extract_boundaries

    try:
        new_bounds = extract_boundaries(path, proposed)
    except Exception:
        return []
    if current is None:
        return new_bounds
    try:
        old_bounds = extract_boundaries(path, current) if current else []
    except Exception:
        old_bounds = []
    old_lines = {b.start_line for b in old_bounds}
    return [b for b in new_bounds if b.start_line not in old_lines]


def _parser_for(path: str):
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    _EXT = {
        "py": "python",
        "ts": "typescript",
        "js": "javascript",
        "go": "go",
        "rs": "rust",
        "java": "java",
    }
    language = _EXT.get(suffix)
    if language is None:
        return None
    try:
        from tracelayer.symbols.registry import get_parser

        return get_parser(language)
    except (ImportError, ValueError):
        return None


def _relpath(root: Path, path: str) -> str:
    """Repo-relative path for obligation identity (Claude sends absolute)."""
    import os

    try:
        rel = os.path.relpath(path, root)
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    return path


def _authoring_block(ctx: HookContext, path: str, payload: dict) -> HookOutput | None:
    """Block the mutation when the proposed edit adds untraced boundaries.

    Deterministic: parse the simulated result, compare symbol lines against
    the current file, and require a marker (or ``# trace:exempt``) on every
    new boundary. Discovery-excluded paths (tests/**, vendor/**) are free.
    """
    if not path or ctx.state is None:
        return None
    state = ctx.state
    try:
        from tracelayer.discovery.ignore import build_ignored

        if build_ignored(ctx.project.root, ctx.project.config, ctx.gitrepo)(path):
            return None
    except Exception:
        pass
    rel_path = _relpath(ctx.project.root, path)
    current = _read_file(ctx.project.root, path)
    proposed = _proposed_text(ctx.project.root, path, payload)
    if proposed is None:
        proposed = current
    if proposed is None:
        return None
    boundaries = _new_boundaries(ctx.project.root, path, current, proposed)
    from tracelayer.discovery.boundaries import boundary_is_traced

    untraced = [b for b in boundaries if not boundary_is_traced(proposed or "", boundaries, b)]
    if not untraced:
        return None
    work = state.active_work(ctx.session_id)
    req = state.active_requirement(ctx.session_id)
    plan = state.active_plan(ctx.session_id)
    boundary = untraced[0]
    line = boundary.start_line
    if not (work or req):
        text = _causal_context_block(boundary, path, line)
        return render_blocked(
            text,
            {
                "event": "pre_mutation",
                "decision": "block",
                "path": path,
                "line": line,
                "new_symbol": boundary.name,
                "output": text,
            },
        )
    state.add_obligation(
        ctx.session_id,
        {
            "path": rel_path,
            "symbol": boundary.name,
            "kind": "new_behavior",
            "work": work or "",
            "requirement": req or "",
            "suggested_marker": _suggested_marker(boundary, work, req, plan),
            "state": "pending",
        },
    )
    text = _authoring_block_text(boundary, path, line, work, req, plan)
    return render_blocked(
        text,
        {
            "event": "pre_mutation",
            "decision": "block",
            "path": path,
            "line": line,
            "new_symbol": boundary.name,
            "obligation": True,
            "output": text,
        },
    )


def _suggested_marker(symbol, work: str | None, req: str | None, plan: str | None = None) -> str:
    work_attr = f" work={work}" if work else ""
    req_attr = f" satisfies={req}" if req else ""
    return f"# trace:v1 id=impl.{symbol.name}{work_attr}{req_attr}"


def _authoring_block_text(
    symbol, path: str, line: int, work: str | None, req: str | None, plan: str | None = None
) -> str:
    lines = [
        "TRACE AUTHORING REQUIRED — NEW BEHAVIOR",
        "",
        "You are creating a new behavioral boundary:",
        f"  {path}:{line}::{symbol.name}",
        "",
        "Why this needs a trace:",
        "  Future agents need a stable link from this implementation back to",
        "  the work and requirement that justify its existence.",
        "",
    ]
    if work or req or plan:
        lines.append("Active context:")
        if work:
            lines.append(f"  Work: {work}")
        if req:
            lines.append(f"  Requirement: {req}")
        if plan:
            lines.append(f"  Plan: {plan}")
        lines.append("")
    lines += [
        "Retry this edit with this marker directly above the function:",
        "",
        f"  {_suggested_marker(symbol, work, req, plan)}",
        "",
        "Do NOT add path=, commit=, test=, or line= — TraceLayer derives",
        "those facts.",
        "",
        "If this function is intentionally trivial/internal and does not",
        f"represent a meaningful behavioral boundary, add `{_EXEMPT_MARK}`",
        "directly above it instead of silently omitting the trace.",
    ]
    return fit("\n".join(lines), 4000)


def _causal_context_block(symbol, path: str, line: int) -> str:
    lines = [
        "TRACE CAUSAL CONTEXT REQUIRED",
        "",
        "You are creating product behavior, but this session has no active",
        "work item or requirement:",
        f"  {path}:{line}::{symbol.name}",
        "",
        "Future agents must be able to answer why this behavior exists.",
        "Before creating it, either:",
        "",
        "  trace task begin <existing-work-id>",
        "",
        "or create the appropriate trace root (requirement/work item) and",
        "establish it with `trace task begin`.",
    ]
    return fit("\n".join(lines), 4000)


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
