"""Diagnostic and deterministic rule registry.

Rule IDs follow spec Section 24.5. Every deterministic check emits a
Diagnostic carrying its rule ID, severity, and remediation so that failures
are explainable (NFR-008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

SEVERITIES = (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO)


@dataclass(frozen=True)
class RuleDef:
    rule_id: str
    summary: str
    severity: str
    remediation: str


RULES: dict[str, RuleDef] = {
    "TL001": RuleDef(
        "TL001",
        "duplicate trace ID",
        SEVERITY_ERROR,
        "Keep exactly one marker declaring this ID, or rename one of the duplicates.",
    ),
    "TL002": RuleDef(
        "TL002",
        "unresolved edge target",
        SEVERITY_ERROR,
        "Create the missing artifact or fix/remove the edge target ID.",
    ),
    "TL003": RuleDef(
        "TL003",
        "detached or ambiguous structural marker",
        SEVERITY_ERROR,
        "Move the marker adjacent to exactly one supported symbol, or annotate the intended element.",
    ),
    "TL004": RuleDef(
        "TL004",
        "malformed marker syntax",
        SEVERITY_ERROR,
        "Rewrite the marker as `trace:v1 key=value ...` per the marker protocol.",
    ),
    "TL005": RuleDef(
        "TL005",
        "invalid trace ID",
        SEVERITY_ERROR,
        "Use only [A-Za-z0-9._:/-] characters and a unique ID.",
    ),
    "TL006": RuleDef(
        "TL006",
        "duplicate key on one marker",
        SEVERITY_ERROR,
        "Declare each key at most once per marker; combine targets with commas.",
    ),
    "TL007": RuleDef(
        "TL007",
        "invalid field value",
        SEVERITY_ERROR,
        "Use a value from the ontology registry (artifact types, edge targets).",
    ),
    "TL010": RuleDef(
        "TL010",
        "changed behavior missing requirement ancestry",
        SEVERITY_ERROR,
        "Link the implementation to its work item or requirement via work=/satisfies=.",
    ),
    "TL011": RuleDef(
        "TL011",
        "changed requirement has stale downstream implementation",
        SEVERITY_ERROR,
        "Review downstream relationships; run `trace review <id>` after confirming they still hold.",
    ),
    "TL012": RuleDef(
        "TL012",
        "new or changed meaningful behavior not traced",
        SEVERITY_ERROR,
        "Create a trace marker at the behavior boundary and link it semantically.",
    ),
    "TL020": RuleDef(
        "TL020",
        "required verification test missing",
        SEVERITY_ERROR,
        "Add a test that verifies the requirement (verifies=...).",
    ),
    "TL021": RuleDef(
        "TL021",
        "linked test did not pass at current revision",
        SEVERITY_ERROR,
        "Run the linked tests and ingest results with `trace evidence ingest`.",
    ),
    "TL022": RuleDef(
        "TL022",
        "exercise claim lacks required execution evidence",
        SEVERITY_ERROR,
        "Run tests with coverage and re-ingest evidence.",
    ),
    "TL030": RuleDef(
        "TL030",
        "traced symbol deleted with unresolved incoming edges",
        SEVERITY_ERROR,
        "Retire the node or update dependents (supersedes=/depends_on=).",
    ),
    "TL040": RuleDef(
        "TL040",
        "unknown marker key",
        SEVERITY_ERROR,
        "Remove the unknown key or switch to migration mode.",
    ),
    "TL050": RuleDef(
        "TL050",
        "evidence revision mismatch",
        SEVERITY_ERROR,
        "Re-ingest evidence bound to the evaluated revision.",
    ),
    "TL051": RuleDef(
        "TL051",
        "evidence parser failure",
        SEVERITY_ERROR,
        "Fix the evidence file; it is treated as untrusted input.",
    ),
    "TL060": RuleDef(
        "TL060",
        "semantic audit required",
        SEVERITY_ERROR,
        "Run the independent semantic auditor and commit its result artifact.",
    ),
    "TL061": RuleDef(
        "TL061",
        "expired waiver",
        SEVERITY_ERROR,
        "Renew the waiver with a new expiry or resolve the underlying failure.",
    ),
    "TL062": RuleDef(
        "TL062",
        "evidence not bound to exact revision",
        SEVERITY_ERROR,
        "Re-ingest evidence bound to the evaluated revision.",
    ),
    "TL063": RuleDef(
        "TL063",
        "enforcement configuration changed",
        SEVERITY_WARNING,
        "Policy/schema changes alter enforcement; ensure this change is intentional and reviewed.",
    ),
    "TL100": RuleDef(
        "TL100", "configuration error", SEVERITY_ERROR, "Fix the configuration file and re-run."
    ),
    "TL110": RuleDef(
        "TL110",
        "required stale node blocks lifecycle",
        SEVERITY_ERROR,
        "Review or re-verify the stale relationship.",
    ),
}


@dataclass
class Diagnostic:
    rule_id: str
    severity: str
    message: str
    trace_id: str | None = None
    path: str | None = None
    line: int | None = None
    remediation: str | None = None
    lifecycle: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "rule": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.trace_id is not None:
            d["trace_id"] = self.trace_id
        if self.path is not None:
            d["path"] = self.path
        if self.line is not None:
            d["line"] = self.line
        if self.remediation is not None:
            d["remediation"] = self.remediation
        if self.lifecycle is not None:
            d["lifecycle"] = self.lifecycle
        if self.metadata:
            d["metadata"] = self.metadata
        return d


def make(
    rule_id: str,
    *,
    message: str | None = None,
    severity: str | None = None,
    remediation: str | None = None,
    **fields: Any,
) -> Diagnostic:
    """Construct a Diagnostic from the rule registry, with optional overrides.

    Unknown rule IDs raise KeyError: deterministic checks must reference
    registered rules so that every diagnostic carries remediation guidance.
    """
    rule = RULES[rule_id]
    return Diagnostic(
        rule_id=rule_id,
        severity=severity if severity is not None else rule.severity,
        message=message if message is not None else rule.summary,
        remediation=remediation if remediation is not None else rule.remediation,
        **fields,
    )


def blocking(diagnostics: list[Diagnostic]) -> bool:
    """True when any diagnostic has ERROR severity."""
    return any(d.severity == SEVERITY_ERROR for d in diagnostics)
