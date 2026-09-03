"""Deterministic artifact planning: intent -> proportional artifacts (spec Sections 19-20).

No model calls: scope derives from task kind, requirement count, prompt
length, and keyword signals. Agents refine the result; the engine guarantees
a sane proportional default (tiny/small/medium/large) with a receipt
(the matched signals are returned, not just the verdict).
"""

from __future__ import annotations

import re

SCOPES = ("tiny", "small", "medium", "large")

_LARGE_SIGNALS = (
    "migrat",
    "replac",
    "distributed",
    "overhaul",
    "rewrite",
    "rearchitect",
    "re-architect",
    "multi-repo",
    "monorepo",
)
_RFC_SIGNALS = ("rfc", "distributed", "service", "protocol", "redesign")
_ADR_SIGNALS = ("architect", "replac", "migrat", "concurren", "performance", "strategy")
_RUNBOOK_SIGNALS = ("deploy", "rollout", "runbook", "operation", "on-call", "oncall", "incident")
_TINY_SIGNALS = ("rename", "typo", "comment", "format", "lint")

_KIND_BASE = {
    "REFACTOR": 0,
    "MAINTENANCE": 0,
    "NON_BEHAVIORAL_EDIT": 0,
    "BUG_CONTRACT_BACKFILL": 1,
    "BEHAVIOR_CHANGE": 1,
    "NEW_FEATURE": 1,
    "FEATURE_EXTENSION": 1,
    "GREENFIELD_PROJECT": 2,
}


# trace:exempt reason=internal-helper
def _hits(text: str, signals: tuple[str, ...]) -> list[str]:
    """Signal substrings present in the lowercased intent (the receipt)."""
    lowered = text.lower()
    return [s for s in signals if s in lowered]


# trace:exempt reason=internal-helper
def classify_scope(prompt: str, kind: str = "new_feature", num_requirements: int = 1) -> dict:
    canon = str(kind or "").strip().upper().replace("-", "_").replace(" ", "_")
    text = str(prompt or "")
    level = _KIND_BASE.get(canon, 1)
    if num_requirements >= 2 and level < 2 and canon not in ("REFACTOR", "MAINTENANCE"):
        level = 2
    large = _hits(text, _LARGE_SIGNALS)
    tiny = _hits(text, _TINY_SIGNALS)
    if num_requirements >= 5 or large:
        level = 3
    elif len(text) >= 800 and level < 2:
        level = 2
    elif tiny and level == 1 and not large and num_requirements <= 1:
        level = 0
    return {"scope": SCOPES[level], "large_signals": large, "tiny_signals": tiny}


# trace:exempt reason=internal-helper
def suggest_questions(prompt: str, limit: int = 5) -> list[str]:
    """Interrogative sentences from the intent (candidates, not nodes).

    Only materiality-judged questions become QUESTION nodes (spec Section 16);
    the engine never auto-creates them from punctuation.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", str(prompt or "").strip())]
    return [p for p in parts if p.endswith("?")][:limit]


# trace:exempt reason=internal-helper
def plan_steps_for(requirement_titles: list[str], scope: str) -> list[str]:
    """One implementation step per requirement plus verification tail."""
    steps = [f"Implement: {title[:200]}" for title in requirement_titles if str(title).strip()]
    if scope != "tiny":
        steps.append("Add or extend verification tests")
        steps.append("Run verification and finalize")
    return steps or ["Implement the requested change"]


# trace:v1 id=impl.planning.artifact-engine work=WORK-knowledge-first-ambient-bootstrap-with-artifact-planning satisfies=REQ-artifact-planning-engine
def plan_artifacts(
    prompt: str, *, kind: str = "new_feature", num_requirements: int = 1
) -> dict:
    """Artifact plan for an intent (spec Section 19 output shape)."""
    text = str(prompt or "")
    scope_info = classify_scope(text, kind, num_requirements)
    scope = scope_info["scope"]
    rfc = _hits(text, _RFC_SIGNALS)
    adr = _hits(text, _ADR_SIGNALS)
    runbook = _hits(text, _RUNBOOK_SIGNALS)
    if scope == "tiny":
        adr_value: object = False
    elif scope == "large" or (adr and scope in ("small", "medium")):
        adr_value = True
    elif scope == "medium":
        adr_value = "possible"
    else:
        adr_value = False
    return {
        "scope": scope,
        "scope_signals": scope_info,
        "work": True,
        "requirements": scope != "tiny",
        "spec": {"tiny": "none", "small": "small", "medium": "full", "large": "full"}[scope],
        "rfc": scope == "large" and bool(rfc),
        "adr": adr_value,
        "plan": scope != "tiny",
        "tasks": scope != "tiny",
        "runbook": scope in ("medium", "large") and bool(runbook),
        "docs_update": scope in ("small", "medium", "large"),
        "suggested_questions": suggest_questions(text) if scope != "tiny" else [],
    }
