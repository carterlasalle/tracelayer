"""Shared hook infrastructure (spec Section 22, FR-024/FR-025).

Hooks are template-driven: graph facts plus fixed text, never free-form LLM
generation. Every piece of repository text injected into output passes through
`sanitize_text` (T1) and every output is hard-bounded by
`hooks.max_context_chars`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tracelayer.config import Project
from tracelayer.hooks.session_state import SessionState

if TYPE_CHECKING:
    from tracelayer.git.repo import GitRepo
    from tracelayer.graph.models import Node
    from tracelayer.graph.store import GraphStore

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Internal hook event -> real Claude Code hook event name (docs require the
# actual event names in hookSpecificOutput).
_CLAUDE_EVENT_NAMES: dict[str, str] = {
    "session_start": "SessionStart",
    "prompt_context": "UserPromptSubmit",
    "pre_mutation": "PreToolUse",
    "post_mutation": "PostToolUse",
    "post_batch": "PostToolBatch",
    "stop": "Stop",
}

# Predicates marking behavior as protected (spec 22.3).
PROTECTING_PREDICATES = ("satisfies", "work")


@dataclass
class HookContext:
    """Everything a hook handler needs for one event."""

    project: Project
    store: GraphStore | None
    gitrepo: GitRepo | None = None
    session_id: str = "default"
    state: SessionState | None = None


@dataclass
# trace:exempt reason=data-container
class HookOutput:
    """Decision plus bounded text plus machine JSON for one hook event."""

    decision: str = "allow"  # "allow" | "block"
    output: str = ""
    json: dict[str, Any] = field(default_factory=dict)

    def render(self, fmt: str) -> str:
        """Render for the requested adapter format."""
        if fmt == "json":
            return json.dumps(self.json, sort_keys=True)
        if fmt == "claude":
            return self._render_claude()
        return self.output

    # trace:v1 id=impl.hooks.claude-render work=WORK-TL-001
    def _render_claude(self) -> str:
        """Claude Code hook contract, per event (docs: code.claude.com/hooks).

        - PreToolUse: decision inside ``hookSpecificOutput`` —
          ``permissionDecision: deny`` + reason (shown to Claude); guidance
          via ``additionalContext``. JSON is printed with exit 0 (the
          sanctioned way to return a structured decision).
        - Stop: top-level ``decision: "block"`` + ``reason``; non-error
          feedback that continues the conversation goes in
          ``hookSpecificOutput.additionalContext``.
        - All other events: ``hookSpecificOutput.additionalContext``.
        """
        event = self.json.get("event", "")
        claude_event = _CLAUDE_EVENT_NAMES.get(event, event)
        if event == "pre_mutation":
            out: dict[str, object] = {"hookEventName": claude_event}
            if self.decision == "block":
                out["permissionDecision"] = "deny"
                out["permissionDecisionReason"] = self.output
            else:
                out["permissionDecision"] = "allow"
                if self.output:
                    out["additionalContext"] = self.output
            return json.dumps({"hookSpecificOutput": out}, sort_keys=True)
        if event == "stop":
            if self.decision == "block":
                return json.dumps({"decision": "block", "reason": self.output}, sort_keys=True)
            if self.output:
                return json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": claude_event,
                            "additionalContext": self.output,
                        }
                    },
                    sort_keys=True,
                )
            return ""
        if self.output:
            return json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": claude_event,
                        "additionalContext": self.output,
                    }
                },
                sort_keys=True,
            )
        return ""


def sanitize_text(text: str, max_chars: int = 200) -> str:
    """Flatten untrusted repository text (T1): collapse whitespace, strip
    control characters, hard-bound, and delimit as repository data."""
    flat = _CONTROL.sub("", " ".join(str(text).split()))
    if len(flat) > max_chars:
        flat = flat[: max_chars - 1] + "\u2026"
    return "repository data: " + flat


def fit(text: str, max_chars: int) -> str:
    """Hard-bound output text; a zero or negative cap yields empty output."""
    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "\u2026"


def resolve_path(root: Path, rel: str) -> Path | None:
    """Resolve a repo-relative path confined to root (T2/T3); None when outside."""
    try:
        path = (root / str(rel)).resolve()
    except (OSError, ValueError):
        return None
    root_res = root.resolve()
    if path != root_res and root_res not in path.parents:
        return None
    return path


def hook_context(
    project: Project,
    store: GraphStore | None,
    gitrepo: GitRepo | None,
    payload: dict,
) -> HookContext:
    """Build a HookContext from the event payload (resolves the session id)."""
    state = SessionState(project)
    return HookContext(project, store, gitrepo, state.session_id_from(payload), state)


# trace:v1 id=impl.hooks.common work=WORK-TL-001
def render_allowed(output: str, json: dict) -> HookOutput:
    """Wrap a non-blocking hook result."""
    return HookOutput(decision="allow", output=output, json=json)


def render_blocked(output: str, json: dict) -> HookOutput:
    """Wrap a blocking hook result (pre-mutation gate, stop gate)."""
    return HookOutput(decision="block", output=output, json=json)


# -- deterministic graph helpers (duck-typed on GraphStore) ------------------


def node_at_path(store: Any, path: str, line: int | None) -> Node | None:
    """Active node for `path`; prefer range containment of `line`, else the
    first node at the path sorted by start line then trace id."""
    candidates = [n for n in store.all_nodes() if n.active and n.canonical_path == path]
    if not candidates:
        return None
    if line is not None:
        containing = [
            n
            for n in candidates
            if n.source_start_line is not None
            and n.source_start_line <= line
            and (n.source_end_line is None or line <= n.source_end_line)
        ]
        if containing:
            return max(containing, key=lambda n: (n.source_start_line or 0, n.trace_id))
    return min(candidates, key=lambda n: (n.source_start_line or 0, n.trace_id))


def is_protected(store: Any, node: Node) -> bool:
    """True when the node carries satisfies/work edges (protected behavior)."""
    return any(store.edges_from(node.entity_uid, predicate=p) for p in PROTECTING_PREDICATES)


def edge_target_ids(store: Any, uid: str, predicates: tuple[str, ...]) -> list[str]:
    """Sorted trace ids reachable from `uid` via outgoing edges of `predicates`."""
    ids: set[str] = set()
    for pred in predicates:
        for e in store.edges_from(uid, predicate=pred):
            t = store.get_node(uid=e.to_uid)
            if t is not None:
                ids.add(t.trace_id)
    return sorted(ids)


def linked_test_ids(store: Any, node: Node, satisfied: list[str]) -> list[str]:
    """Tests verifying the satisfied requirements or exercising the node."""
    uids: set[str] = set()
    for req in satisfied:
        req_uid = store.get_node_uid(req)
        if req_uid:
            uids.update(e.from_uid for e in store.edges_to(req_uid, predicate="verifies"))
    uids.update(e.from_uid for e in store.edges_to(node.entity_uid, predicate="exercises"))
    tests = [t for u in uids if (t := store.get_node(uid=u)) is not None]
    return sorted(t.trace_id for t in tests)
