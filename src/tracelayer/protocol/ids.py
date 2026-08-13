"""Stable trace ID rules (spec FR-002, 11.6)."""

from __future__ import annotations

import re

ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/\-]+$")

_PATTERN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^REQ-"), "requirement"),
    (re.compile(r"^NFR-"), "nfr"),
    (re.compile(r"^ADR-"), "decision"),
    (re.compile(r"^WORK-"), "work"),
    (re.compile(r"^PLAN-"), "plan"),
    (re.compile(r"^impl\."), "implementation"),
    (re.compile(r"^test\."), "test"),
    (re.compile(r"^ops\."), "operation"),
    (re.compile(r"^doc\."), "document"),
    (re.compile(r"^runbook\."), "runbook"),
    (re.compile(r"^prompt\."), "prompt"),
    (re.compile(r"^config\."), "config"),
    (re.compile(r"^data\."), "data"),
    (re.compile(r"^PRD-"), "prd"),
    (re.compile(r"^prd\."), "prd"),
    (re.compile(r"^GOAL-"), "goal"),
    (re.compile(r"^goal\."), "goal"),
    (re.compile(r"^EV-"), "evidence"),
    (re.compile(r"^EVIDENCE-"), "evidence"),
    (re.compile(r"^CI-"), "ci_run"),
]

# ID prefix used by `trace new <type>` when generating new identities.
TYPE_PREFIX = {
    "requirement": "REQ-",
    "nfr": "NFR-",
    "decision": "ADR-",
    "work": "WORK-",
    "plan": "PLAN-",
    "implementation": "impl.",
    "test": "test.",
    "document": "doc.",
    "runbook": "runbook.",
    "operation": "ops.",
    "prompt": "prompt.",
    "config": "config.",
    "data": "data.",
    "goal": "goal.",
    "prd": "PRD-",
}


def is_valid_id(value: str) -> bool:
    """True when `value` is a valid v1 trace ID ([A-Za-z0-9._:/-]+)."""
    return bool(ID_PATTERN.match(value))


def infer_node_type(trace_id: str) -> str | None:
    """Deterministic artifact-type inference from the ID namespace (OQ-002)."""
    for pattern, node_type in _PATTERN_RULES:
        if pattern.match(trace_id):
            return node_type
    return None


def generate_id(node_type: str, name: str, taken: set[str] | None = None) -> str:
    """Generate a schema-compliant ID for `node_type` from a human name (FR-020).

    Slugs non-alphanumeric characters to `-` (dot for lowercase namespaces) and
    appends `-N` until unique against `taken`.
    """
    prefix = TYPE_PREFIX.get(node_type, f"{node_type}.")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "artifact"
    base = f"{prefix}{slug}"
    if not taken:
        return base
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate
