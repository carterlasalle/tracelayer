"""Artifact template registry: recommended shape per engineering artifact (spec Section 67).

Templates guide proportional documentation (spec Sections 21-28); only the
``required`` sections are structurally validated. Prose quality stays with
the semantic auditor, never with hard-coded length rules (spec Section 68).
"""

from __future__ import annotations

TEMPLATE_TYPES = (
    "work",
    "task",
    "question",
    "decision",
    "requirement",
    "spec",
    "rfc",
    "adr",
    "plan",
    "runbook",
    "guide",
    "reference",
    "migration",
    "incident",
)

_TEMPLATES: dict[str, dict] = {
    "work": {
        "sections": ["objective", "scope", "tasks", "acceptance", "current state"],
        "required": ["objective"],
        "states": ["ACTIVE", "DONE", "PARTIALLY_COMPLETE", "DEFERRED", "CANCELLED"],
        "relationships": ["contains", "blocks", "discovered_from"],
    },
    "task": {
        "sections": ["objective", "acceptance", "completed", "remaining", "blocked by"],
        "required": ["objective"],
        "states": [
            "TODO",
            "READY",
            "IN_PROGRESS",
            "PARTIALLY_COMPLETE",
            "BLOCKED",
            "WAITING_FOR_DECISION",
            "WAITING_FOR_INPUT",
            "DEFERRED",
            "DONE",
            "CANCELLED",
            "NOT_IMPLEMENTED",
        ],
        "relationships": ["blocked_by", "depends_on", "blocks", "asks", "discovered_from"],
    },
    "question": {
        "sections": ["question", "context", "blocks", "answered by"],
        "required": ["question"],
        "states": ["OPEN", "ANSWERED", "SUPERSEDED", "NO_LONGER_RELEVANT", "DEFERRED"],
        "relationships": ["blocks", "related_to", "answered_by"],
    },
    "decision": {
        "sections": ["decision", "reason", "alternatives", "consequences"],
        "required": ["decision"],
        "states": ["PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED"],
        "relationships": ["answers", "addresses", "supersedes"],
    },
    "requirement": {
        "sections": ["statement", "acceptance criteria"],
        "required": ["statement"],
        "states": [
            "UNIMPLEMENTED",
            "PARTIALLY_IMPLEMENTED",
            "IMPLEMENTED",
            "VERIFIED",
            "STALE",
            "DEPRECATED",
        ],
        "relationships": ["derived_from", "supersedes", "addresses"],
    },
    "spec": {
        "sections": [
            "summary",
            "problem",
            "context",
            "goals",
            "non-goals",
            "intended behavior",
            "requirements",
            "interfaces",
            "test strategy",
            "open questions",
        ],
        "required": ["summary", "requirements"],
        "states": ["DRAFT", "ACCEPTED", "SUPERSEDED"],
        "relationships": ["contains", "derived_from", "addresses"],
    },
    "rfc": {
        "sections": [
            "status",
            "summary",
            "problem",
            "motivation",
            "proposed design",
            "interfaces",
            "failure modes",
            "alternatives",
            "compatibility",
            "migration",
            "open questions",
        ],
        "required": ["status", "summary", "proposed design", "alternatives"],
        "states": ["DRAFT", "PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED"],
        "relationships": ["proposes", "supersedes", "related_to"],
    },
    "adr": {
        "sections": [
            "status",
            "context",
            "decision",
            "alternatives considered",
            "consequences",
            "operational implications",
            "related requirements",
        ],
        "required": ["status", "decision"],
        "states": ["PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED", "DEPRECATED"],
        "relationships": ["addresses", "supersedes", "decides"],
    },
    "plan": {
        "sections": [
            "objective",
            "scope",
            "phases",
            "tasks",
            "dependencies",
            "acceptance",
            "questions",
            "current state",
        ],
        "required": ["objective", "tasks"],
        "states": ["DRAFT", "ACTIVE", "DONE", "SUPERSEDED", "CANCELLED"],
        "relationships": ["contains", "depends_on", "blocks"],
    },
    "runbook": {
        "sections": ["purpose", "prerequisites", "procedure", "rollback", "troubleshooting"],
        "required": ["procedure"],
        "states": ["DRAFT", "ACTIVE", "DEPRECATED"],
        "relationships": ["documents", "related_to"],
    },
    "guide": {
        "sections": ["purpose", "prerequisites", "steps", "troubleshooting"],
        "required": ["steps"],
        "states": ["DRAFT", "ACTIVE", "DEPRECATED"],
        "relationships": ["documents", "related_to"],
    },
    "reference": {
        "sections": ["overview", "interface", "examples"],
        "required": ["interface"],
        "states": ["DRAFT", "ACTIVE", "DEPRECATED"],
        "relationships": ["documents", "related_to"],
    },
    "migration": {
        "sections": ["scope", "mapping", "procedure", "rollback", "verification"],
        "required": ["mapping", "procedure", "rollback"],
        "states": ["DRAFT", "ACTIVE", "DONE", "DEPRECATED"],
        "relationships": ["supersedes", "documents", "related_to"],
    },
    "incident": {
        "sections": ["summary", "impact", "timeline", "root cause", "remediation", "follow-up"],
        "required": ["summary", "root cause", "remediation"],
        "states": ["OPEN", "INVESTIGATING", "RESOLVED", "RETIRED"],
        "relationships": ["discovered_from", "related_to", "resolves"],
    },
}


# trace:exempt reason=internal-helper
def template_types() -> list[str]:
    """Registered artifact types in stable order."""
    return list(TEMPLATE_TYPES)


# trace:exempt reason=internal-helper
def get_template(node_type: str) -> dict | None:
    """Template for ``node_type``, or None when unregistered."""
    template = _TEMPLATES.get(str(node_type or "").lower())
    return dict(template) if template is not None else None


# trace:v1 id=impl.templates.validate work=WORK-documentation-artifact-system-and-useful-context-engine satisfies=REQ-artifact-template-registry
def validate_structure(node_type: str, headings: list[str]) -> list[str]:
    """Required sections missing from ``headings`` (deterministic, spec Section 68).

    Matching is case-insensitive on stripped heading text; unknown artifact
    types have no required sections.
    """
    template = _TEMPLATES.get(str(node_type or "").lower())
    if template is None:
        return []
    present = {str(h or "").strip().lower() for h in headings}
    return [section for section in template["required"] if section not in present]
