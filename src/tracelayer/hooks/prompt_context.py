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
_PLAN_ID = re.compile(r"\b(PLAN-[A-Za-z0-9][A-Za-z0-9._/-]*)\b")


# trace:v1 id=impl.hooks.prompt-context work=WORK-TL-001
def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Natural-language intake (adversarial review P0).

    Every UserPromptSubmit runs the deterministic resolver:

    - an explicit WORK-/REQ-/PLAN- id in the prompt activates it directly;
    - a strong resolve match activates the work + its requirements + plan
      automatically (no ID ceremony);
    - new intent with no causal context records a pending-bootstrap marker,
      which the pre-mutation gate turns into a mandatory semantic-bootstrap
      instruction before the first code mutation.

    The search results are injected as context only when there are hits.
    """
    json_data = {
        "event": "prompt_context",
        "decision": "allow",
        "output": "",
        "results": [],
        "active_work": None,
        "active_requirement": None,
        "active_plan": None,
        "intake": None,
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
    plan = _PLAN_ID.search(prompt)
    if plan is not None:
        ctx.state.set_active_plan(ctx.session_id, plan.group(1))
        json_data["active_plan"] = plan.group(1)
    if work is None and req is None:
        from tracelayer.tasks import activate as ambient_activate
        from tracelayer.tasks import resolve as ambient_resolve

        try:
            from tracelayer.git.repo import GitRepo

            resolution = ambient_resolve(
                ctx.project,
                ctx.store,
                prompt,
                session_id=ctx.session_id,
                gitrepo=ctx.gitrepo or GitRepo.open(ctx.project.root),
            )
        except Exception:
            resolution = {"resolution": "new", "confidence": 0.0}
        if resolution.get("resolution") == "resume" and resolution.get("work"):
            try:
                act = ambient_activate(ctx.project, ctx.store, resolution["work"], ctx.session_id)
                json_data["active_work"] = act["work"]
                json_data["active_requirement"] = (
                    act["requirements"][0] if act["requirements"] else None
                )
                json_data["active_plan"] = act["plan"]
                json_data["intake"] = "resumed"
            except Exception:
                pass
        else:
            # New intent: record the pending bootstrap so the pre-mutation
            # gate turns the first code mutation into a semantic bootstrap.
            prompt_tokens = re.findall(r"[a-z0-9]+", prompt.lower())
            meaningful = [t for t in prompt_tokens if t not in _STOPWORDS]
            request_like = any(t in _REQUEST_TOKENS for t in prompt_tokens)
            if (
                len(meaningful) >= 3
                and request_like
                and ctx.state.active_work(ctx.session_id) is None
                and ctx.state.pending_bootstrap(ctx.session_id) is None
            ):
                import hashlib

                ctx.state.set_pending_bootstrap(
                    ctx.session_id, hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
                )
                json_data["intake"] = "needs_bootstrap"
    limit = ctx.project.config.hooks.prompt_search_limit
    try:
        results = ctx.store.search(prompt, limit=limit)
    except Exception:
        results = []
    if not results and not json_data["intake"]:
        return render_allowed("", json_data)
    lines = []
    if json_data["intake"] == "needs_bootstrap":
        lines.append(
            "New intent detected: bootstrap a traced task from the user's request"
            ' before the first code mutation (`trace task bootstrap --prompt "..."`).'
        )
    elif json_data["intake"] == "resumed":
        lines.append(
            f"Continuing work item {json_data['active_work']} with all its requirements active."
        )
    if results:
        lines.append("Potential trace context:")
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


_REQUEST_TOKENS = frozenset(
    {
        "build",
        "create",
        "make",
        "add",
        "fix",
        "write",
        "implement",
        "scan",
        "support",
        "allow",
        "list",
        "show",
        "find",
        "search",
        "compare",
        "refactor",
        "update",
        "change",
        "remove",
        "delete",
        "configure",
        "run",
        "test",
        "verify",
        "connect",
        "export",
        "import",
        "convert",
        "generate",
        "improve",
        "speed",
        "upgrade",
        "install",
        "setup",
        "handle",
        "parse",
        "rank",
        "measure",
        "migrate",
        "optimize",
    }
)


_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "with",
        "to",
        "it",
        "this",
        "that",
        "of",
        "in",
        "on",
        "is",
        "are",
        "was",
        "be",
        "make",
        "add",
        "fix",
        "change",
        "continue",
        "work",
        "also",
        "ignore",
        "using",
        "use",
        "so",
        "then",
        "now",
        "but",
        "not",
        "what",
        "when",
        "how",
        "please",
        "can",
        "you",
        "i",
        "me",
        "my",
        "we",
        "our",
        "do",
        "does",
        "did",
        "would",
        "could",
        "should",
        "will",
        "just",
        "like",
        "want",
        "need",
        "build",
        "help",
    }
)
