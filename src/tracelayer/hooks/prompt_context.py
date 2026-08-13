"""UserPromptSubmit hook (spec 22.2, FR-024): orient via deterministic FTS."""

from __future__ import annotations

from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    fit,
    render_allowed,
    sanitize_text,
)


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Search the prompt; inject nothing when there are no hits."""
    json_data = {
        "event": "prompt_context",
        "decision": "allow",
        "output": "",
        "results": [],
    }
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt or ctx.store is None:
        return render_allowed("", json_data)
    limit = ctx.project.config.hooks.prompt_search_limit
    try:
        results = ctx.store.search(prompt, limit=limit)
    except Exception:
        results = []
    if not results:
        return render_allowed("", json_data)
    lines = ["Potential trace context:"]
    for node in results:
        if node.title:
            lines.append(f"- {node.trace_id}: {sanitize_text(node.title, 160)}")
        else:
            lines.append(f"- {node.trace_id}")
        json_data["results"].append(
            {
                "trace_id": node.trace_id,
                "title": sanitize_text(node.title, 200) if node.title else "",
            }
        )
    lines.append("Inspect these before creating new trace identities.")
    text = fit("\n".join(lines), ctx.project.config.hooks.max_context_chars)
    json_data["output"] = text
    return render_allowed(text, json_data)
