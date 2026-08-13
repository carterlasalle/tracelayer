"""UserPromptSubmit hook (spec 22.2, FR-024): orient via deterministic FTS."""

from __future__ import annotations

import re

from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    fit,
    render_allowed,
    sanitize_text,
)

_WORK_ID = re.compile(r"\b(WORK-[A-Za-z0-9][A-Za-z0-9._/-]*)\b")
_REQ_ID = re.compile(r"\b(REQ-[A-Za-z0-9][A-Za-z0-9._/-]*)\b")


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Search the prompt; inject nothing when there are no hits.

    Also records the work item / requirement named in the prompt into
    session state, so later hooks attach new artifacts to it automatically
    (spec 22.4) without the agent hunting for IDs.
    """
    json_data = {
        "event": "prompt_context",
        "decision": "allow",
        "output": "",
        "results": [],
        "active_work": None,
        "active_requirement": None,
    }
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt or ctx.store is None or ctx.state is None:
        return render_allowed("", json_data)
    work = _WORK_ID.search(prompt)
    if work is not None:
        ctx.state.set_active_work(ctx.session_id, work.group(1))
        json_data["active_work"] = work.group(1)
    req = _REQ_ID.search(prompt)
    if req is not None:
        ctx.state.set_active_requirement(ctx.session_id, req.group(1))
        json_data["active_requirement"] = req.group(1)
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
