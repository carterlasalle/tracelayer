"""PostToolBatch hook (spec 22.6, FR-026): grouped edit impact summary."""

from __future__ import annotations

from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    edge_target_ids,
    fit,
    render_allowed,
)


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """One grouped injection summarizing the batch from session state."""
    paths = [str(p) for p in payload.get("paths", [])][:50]
    json_data = {
        "event": "post_batch",
        "decision": "allow",
        "paths": paths,
        "output": "",
    }
    if ctx.store is None or ctx.state is None:
        return render_allowed("", json_data)
    dirty = ctx.state.dirty(ctx.session_id)
    if not dirty or not paths:
        return render_allowed("", json_data)
    path_set = set(paths)
    nodes = {n.trace_id: n for n in ctx.store.all_nodes() if n.trace_id in dirty}
    changed = sorted(
        n.trace_id for n in nodes.values() if n.canonical_path in path_set
    ) or sorted(dirty)
    reqs = sorted(
        {
            r
            for n in nodes.values()
            for r in edge_target_ids(ctx.store, n.entity_uid, ("satisfies", "work"))
        }
    )
    lines = ["TRACE IMPACT OF EDIT BATCH", f"Changed: {', '.join(changed[:10])}"]
    if reqs:
        lines.append(f"Affected requirements: {', '.join(reqs[:6])}")
    lines.append(f"Remaining required verification: {', '.join(sorted(dirty)[:8])}")
    text = fit("\n".join(lines), ctx.project.config.hooks.max_context_chars)
    json_data["output"] = text
    return render_allowed(text, json_data)
