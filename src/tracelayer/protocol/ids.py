"""Stable trace ID rules (spec FR-002, 11.6)."""

from __future__ import annotations

import re

ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/\-]+$")

_PATTERN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^REQ-"), "requirement"),
    (re.compile(r"^NFR-"), "nfr"),
    (re.compile(r"^ADR-"), "decision"),
    (re.compile(r"^DEC-"), "decision"),
    (re.compile(r"^WORK-"), "work"),
    (re.compile(r"^TASK-"), "task"),
    (re.compile(r"^Q-"), "question"),
    (re.compile(r"^SPEC-"), "spec"),
    (re.compile(r"^RFC-"), "rfc"),
    (re.compile(r"^PLAN-"), "plan"),
    (re.compile(r"^PSTEP-"), "plan_step"),
    (re.compile(r"^FIND-"), "finding"),
    (re.compile(r"^LEARN-"), "learning"),
    (re.compile(r"^ANTI-"), "anti_pattern"),
    (re.compile(r"^CONV-"), "convention"),
    (re.compile(r"^CONSTRAINT-"), "constraint"),
    (re.compile(r"^FACT-"), "fact"),
    (re.compile(r"^VALUE-"), "value"),
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
TYPE_PREFIX = {
    "requirement": "REQ-",
    "nfr": "NFR-",
    "decision": "ADR-",
    "work": "WORK-",
    "task": "TASK-",
    "question": "Q-",
    "spec": "SPEC-",
    "rfc": "RFC-",
    "plan": "PLAN-",
    "plan_step": "PSTEP-",
    "finding": "FIND-",
    "learning": "LEARN-",
    "anti_pattern": "ANTI-",
    "convention": "CONV-",
    "constraint": "CONSTRAINT-",
    "fact": "FACT-",
    "value": "VALUE-",
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


# trace:v1 id=impl.protocol.work-model-ids work=WORK-trace-layer-native-work-task-question-decision-model satisfies=REQ-native-work-task-question-decision-ontology
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
