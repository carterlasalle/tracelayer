"""PreToolUse Write/Edit hook — authoring + context gate (spec 22.3, FR-025).

Two gates, both evaluated BEFORE the mutation lands:

1. **Authoring gate (P0)**: the proposed edit is simulated and parsed. New
   behavioral boundaries (functions/classes/methods) without a trace marker
   or an explicit ``# trace:exempt reason=internal-detail`` are BLOCKED with the exact marker to
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


def _classify_boundaries(path: str, current: str | None, proposed: str) -> list[tuple]:
    """(boundary, kind) for the proposed edit, by identity not line numbers.

    Identity = name + kind + parent range + semantic fingerprint, so
    inserting a comment cannot reclassify existing functions, and a
    rewritten body at the same position counts as MODIFIED, not NEW.
    """
    from tracelayer.discovery.boundaries import extract_boundaries
    from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint

    # trace:exempt reason=internal-helper
    def fp(b):
        return semantic_fingerprint(normalize_block(b.source))

    try:
        new_bounds = extract_boundaries(path, proposed)
    except Exception:
        return []
    if current is None:
        return [(b, "NEW") for b in new_bounds]
    try:
        old_bounds = extract_boundaries(path, current) if current else []
    except Exception:
        old_bounds = []
    base = {b.qualified_name or b.name: b for b in old_bounds}
    out: list[tuple] = []
    for b in new_bounds:
        prior = base.get(b.qualified_name or b.name)
        if prior is None:
            out.append((b, "NEW"))
        elif fp(prior) != fp(b):
            out.append((b, "MODIFIED"))
        else:
            out.append((b, "UNCHANGED"))
    return out


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


# trace:exempt reason=internal-helper
def _authoring_block(ctx: HookContext, path: str, payload: dict) -> HookOutput | None:
    """Block the mutation when the proposed edit adds untraced boundaries.

    Deterministic: parse the simulated result, compare symbol lines against
    the current file, and require a marker (or ``# trace:exempt reason=internal-detail``) on every
    new boundary. Discovery-excluded paths (tests/**, vendor/**) are free.

    Intake gates run first (adversarial review P0): a pending bootstrap
    blocks the first code mutation with a semantic-bootstrap instruction,
    and a pending spec update blocks implementation edits until the
    governing requirement's text actually changes.
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
    # -- intake gate: spec update before implementation -------------------
    if ctx.store is not None and state.pending_spec_update(ctx.session_id):
        from tracelayer.tasks import _reconcile_spec_updates

        remaining = _reconcile_spec_updates(ctx.project, ctx.store, ctx.session_id)
        if remaining:
            is_spec = any(
                node.canonical_path == rel_path
                for node in ctx.store.all_nodes(active_only=True)
                if node.node_type in ("requirement", "prd") and node.trace_id in remaining
            )
            if not is_spec:
                text = _spec_update_block(rel_path, remaining)
                return render_blocked(
                    text,
                    {
                        "event": "pre_mutation",
                        "decision": "block",
                        "path": path,
                        "output": text,
                    },
                )
    classified = _classify_boundaries(path, current, proposed)
    from tracelayer.discovery.boundaries import boundary_is_traced

    untraced = [
        (b, kind)
        for b, kind in classified
        if kind in ("NEW", "MODIFIED")
        and not boundary_is_traced(
            proposed or "", [x[0] for x in classified], b, ctx.project.root, ctx.store
        )
    ]
    if not untraced:
        return None
    work = state.active_work(ctx.session_id)
    reqs = state.active_requirements(ctx.session_id)
    req = reqs[0] if len(reqs) == 1 else None
    plan = state.active_plan(ctx.session_id)
    from tracelayer.discovery.suggest import resolve_exercised

    exercised = resolve_exercised(ctx.store, req)
    boundary, change_kind = untraced[0]
    line = boundary.start_line
    if not (work or req):
        if state.pending_bootstrap(ctx.session_id):
            text = _bootstrap_block(boundary, path, line)
        else:
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
    rewrite = None
    if "content" in payload:
        # Automatic updatedInput rewriting is Write-only (review P0): an
        # Edit's old_string/new_string cannot carry a whole-file rewrite
        # without corrupting the target, so Edits fall back to deny + plan.
        rewrite = _deterministic_rewrite(
            state, ctx.session_id, rel_path, path, payload, proposed, untraced
        )
    existing_ids = _file_marker_ids(proposed)
    for boundary, change_kind in untraced[:20]:
        state.add_obligation(
            ctx.session_id,
            {
                "path": rel_path,
                "symbol": boundary.qualified_name or boundary.name,
                "kind": "new_behavior" if change_kind == "NEW" else "modified_behavior",
                "work": work or "",
                "requirement": req or "",
                "suggested_marker": _suggested_marker(
                    boundary, work, req, plan, rel_path, exercised, existing_ids
                ),
                "state": "pending",
            },
        )
    text = _authoring_plan_text(untraced, rel_path, work, reqs, plan, exercised)
    payload_out = {
        "event": "pre_mutation",
        "decision": "block",
        "path": path,
        "line": line,
        "new_symbol": boundary.name,
        "obligation": True,
        "output": text,
    }
    if rewrite is not None:
        payload_out["suggested_rewrite"] = rewrite
    return render_blocked(text, payload_out)


# trace:exempt reason=internal-helper
def _suggested_marker(
    symbol,
    work: str | None,
    req: str | None,
    plan: str | None = None,
    path: str = "",
    exercised: str | None = None,
    existing_ids: set[str] | None = None,
) -> str:
    """Canonical, artifact-aware marker via the shared suggestion engine."""
    from tracelayer.discovery.suggest import suggest_marker

    suggestion = suggest_marker(
        symbol,
        path,
        work=work,
        requirement=req,
        plan=plan,
        exercised=exercised,
        existing_ids=existing_ids,
    )
    return suggestion.marker


# trace:exempt reason=internal-helper
def _deterministic_rewrite(
    state,
    session_id: str,
    rel_path: str,
    path: str,
    payload: dict,
    proposed: str | None,
    untraced: list,
):
    """The WRITE input rewritten with markers injected (Ambient §19).

    Deterministic only: a single active requirement (so the semantic
    mapping is unambiguous) and a language that supports in-file markers
    (JSON uses the sidecar and must fall back to block/retry). Edits are
    NEVER rewritten: an Edit's old_string/new_string cannot carry a
    whole-file rewrite without corrupting the target (adversarial review
    P0) — they fall back to deny + the authoring plan.
    """
    from tracelayer.discovery.suggest import suggest_marker

    if proposed is None:
        return None
    if "old_string" in payload:
        return None  # Edit: deny -> agent retries with the marker
    if len(state.active_requirements(session_id)) != 1:
        return None  # ambiguous requirement mapping: agent must decide
    work = state.active_work(session_id)
    req = state.active_requirements(session_id)[0]
    plan = state.active_plan(session_id)
    existing_ids = _file_marker_ids(proposed)
    insertions: list[tuple[int, str]] = []
    for boundary, _kind in untraced:
        suggestion = suggest_marker(
            boundary,
            path,
            work=work,
            requirement=req,
            plan=plan,
            existing_ids=existing_ids,
        )
        if not suggestion.marker:
            return None
        if suggestion.sidecar:
            return None  # JSON cannot be rewritten in place
        if boundary.language == "markdown":
            insertions.append((boundary.start_line, suggestion.marker))
        else:
            insertions.append((boundary.start_line - 1, suggestion.marker))
    lines = proposed.splitlines()
    for index, marker in sorted(insertions, reverse=True):
        if 0 <= index <= len(lines):
            lines.insert(index, marker)
    rewritten = "\n".join(lines)
    if "content" in payload:
        return {"file_path": rel_path, "content": rewritten}
    return None


# trace:exempt reason=internal-helper
def _authoring_plan_text(
    untraced: list,
    rel_path: str,
    work: str | None,
    reqs: list[str],
    plan: str | None,
    exercised: str | None,
) -> str:
    """One mutation -> the FULL authoring plan (every boundary, not just one).

    With more than one active requirement the engine refuses to attribute a
    single arbitrary requirement (adversarial review P0): the candidate
    list is injected and the agent picks the correct one per boundary —
    the LLM selects semantics, TraceLayer validates the IDs.
    """
    req = reqs[0] if len(reqs) == 1 else None
    lines = [
        "TRACE AUTHORING REQUIRED",
        "",
        f"This mutation creates/rewrites {len(untraced)} untraced boundary(ies):",
        "",
    ]
    for idx, (boundary, change_kind) in enumerate(untraced[:8], start=1):
        verb = "new" if change_kind == "NEW" else "modified untraced"
        lines.append(f"{idx}. {boundary.qualified_name or boundary.name} ({verb})")
        lines.append(
            f"   Add above it: {_suggested_marker(boundary, work, req, plan, rel_path, exercised)}"
        )
    if len(reqs) > 1:
        lines += [
            "",
            "Candidate requirements (choose the correct one(s) per boundary):",
        ]
        lines += [f"  - {rid}" for rid in reqs]
        lines.append(
            "  Add the chosen satisfies= to each boundary's marker (or omit it "
            "when the boundary serves the work as a whole)."
        )
    if len(untraced) > 8:
        lines.append(f"   ... and {len(untraced) - 8} more")
    lines += [
        "",
        "Why: future agents need a stable link from each behavior back to the",
        "work and requirement that justify its existence.",
        "",
        "Do NOT add path=, commit=, test=, or line= — TraceLayer derives them.",
        "",
        "Retry the mutation with every boundary above accounted for, or add an",
        f"explicit `{_EXEMPT_MARK} reason=<why>` for genuinely trivial code.",
    ]
    return fit("\n".join(lines), 6000)


# trace:exempt reason=internal-helper
def _bootstrap_block(symbol, path: str, line: int) -> str:
    """Pending-bootstrap gate: the prompt hook already resolved this request
    as new intent, so the FIRST code mutation must be preceded by a
    semantic bootstrap — not by a generic search-and-hope recovery
    (adversarial review P0: natural-language intake).
    """
    lines = [
        "AMBIENT TRACE BOOTSTRAP REQUIRED — NEW INTENT, NO CAUSAL CONTEXT",
        "",
        "Your session resolved this request as new intent, and no work item",
        "or requirement exists yet. Before writing implementation code:",
        f"  {path}:{line}::{symbol.name}",
        "",
        "1. Run a semantic bootstrap derived from the user's request:",
        "",
        '     trace task bootstrap --prompt "<the user\'s request>"',
        "",
        "   or author the semantic bundle yourself (title, kind, intent,",
        "   requirements with titles + statements, optional plan steps):",
        "",
        "     trace task bootstrap --json < <bundle>",
        "",
        "2. Confirm the session context (`trace task context`): the work,",
        "   requirements, and plan are now active.",
        "3. Retry this mutation with boundary-local traces.",
        "",
        "If this request actually continues an existing task, run",
        '`trace task resolve --prompt "<request>"` and `trace task activate',
        "<WORK-ID>` instead — never ask the user for TraceLayer IDs.",
    ]
    return fit("\n".join(lines), 4000)


# trace:exempt reason=internal-helper
def _spec_update_block(rel_path: str, req_ids: list[str]) -> str:
    """Intake gate: the user changed a requirement's contract; implementation
    edits are blocked until the governing requirement text actually changes
    (adversarial review P0: spec evolution is enforced, not voluntary)."""
    lines = [
        "SPEC UPDATE REQUIRED",
        "",
        "This request changes the contract of an existing requirement, so the",
        "requirement must change BEFORE the implementation does:",
    ]
    lines += [f"  - {rid}" for rid in req_ids[:8]]
    lines += [
        "",
        "1. Edit the requirement (its statement or acceptance criteria) in",
        "   its source document so the spec reflects the new contract.",
        "2. Re-run `trace index --changed` (or let the hooks index it).",
        "3. Reclassify with `trace task intake --kind implementation-only`",
        "   once the spec matches the new intent.",
        "",
        "This mutation (to a non-spec path) stays blocked until then.",
    ]
    return fit("\n".join(lines), 4000)


# trace:exempt reason=internal-helper
def _file_marker_ids(text: str) -> set[str]:
    """Trace ids already present in the proposed file (id collision guard)."""
    try:
        from tracelayer.protocol import iter_marker_hits, parse_marker_hit

        ids: set[str] = set()
        for hit in iter_marker_hits(text, "proposed"):
            res = parse_marker_hit(hit, unknown_keys="warn")
            if res.marker is not None and res.marker.trace_id:
                ids.add(res.marker.trace_id)
        return ids
    except Exception:
        return set()


# trace:exempt reason=internal-helper
def _causal_context_block(symbol, path: str, line: int) -> str:
    lines = [
        "AMBIENT TRACE BOOTSTRAP REQUIRED",
        "",
        "This mutation introduces new product behavior and no causal",
        "context exists:",
        f"  {path}:{line}::{symbol.name}",
        "",
        "Before retrying:",
        "",
        "1. search existing intent (`trace search`);",
        "2. if none exists, bootstrap a work item and minimal specification",
        "   from the user's current request:",
        "     trace task bootstrap --json < <bundle>",
        "   (title, kind, intent, requirements with titles + statements,",
        "   optional plan steps);",
        "3. activate the resulting requirements;",
        "4. retry this mutation with boundary-local traces.",
        "",
        "Do not ask the user for TraceLayer IDs.",
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
