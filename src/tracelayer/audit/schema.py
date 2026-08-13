"""JSON Schema documents for the audit package and audit result (spec 30.2/30.3)."""

from __future__ import annotations

AUDIT_PACKAGE_SCHEMA = "tracelayer-audit-package/v1"
AUDIT_RESULT_SCHEMA = "tracelayer-audit-result/v1"


def audit_package_schema() -> dict:
    """JSON Schema (draft-07) describing the bounded auditor input package."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://tracelayer.dev/schemas/audit-package-v1.json",
        "title": "TraceLayer Audit Package",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "work",
            "deterministic_status",
            "changed_nodes",
            "requirements",
            "decisions",
            "implementations",
            "tests",
            "evidence_summary",
            "trace_paths",
            "unexpected_changes",
        ],
        "properties": {
            "schema": {"const": AUDIT_PACKAGE_SCHEMA},
            "work": {"type": ["string", "null"]},
            "deterministic_status": {"enum": ["pass", "fail"]},
            "changed_nodes": {"type": "array", "items": {"type": "string"}},
            "requirements": {
                "type": "array",
                "items": {"$ref": "#/definitions/requirement"},
            },
            "decisions": {
                "type": "array",
                "items": {"$ref": "#/definitions/excerpt_node"},
            },
            "implementations": {
                "type": "array",
                "items": {"$ref": "#/definitions/implementation"},
            },
            "tests": {"type": "array", "items": {"$ref": "#/definitions/test"}},
            "evidence_summary": {"type": "object"},
            "trace_paths": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "unexpected_changes": {"type": "array", "items": {"type": "string"}},
        },
        "definitions": {
            "requirement": {
                "type": "object",
                "required": ["id", "excerpt", "fingerprint"],
                "properties": {
                    "id": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "fingerprint": {"type": "string"},
                },
            },
            "excerpt_node": {
                "type": "object",
                "required": ["id", "excerpt"],
                "properties": {
                    "id": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
            },
            "implementation": {
                "type": "object",
                "required": ["id", "symbol", "source_excerpt"],
                "properties": {
                    "id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                },
            },
            "test": {
                "type": "object",
                "required": ["id", "source_excerpt"],
                "properties": {
                    "id": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                    "result": {"type": "string"},
                },
            },
        },
    }


def audit_result_schema() -> dict:
    """JSON Schema (draft-07) for the auditor's independent verdict (spec 30.3)."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://tracelayer.dev/schemas/audit-result-v1.json",
        "title": "TraceLayer Audit Result",
        "type": "object",
        "required": ["status", "findings"],
        "properties": {
            "schema": {"enum": [AUDIT_RESULT_SCHEMA]},
            "status": {"enum": ["pass", "fail", "uncertain"]},
            "findings": {
                "type": "array",
                "items": {"$ref": "#/definitions/finding"},
            },
        },
        "definitions": {
            "finding": {
                "type": "object",
                "required": ["severity", "claim"],
                "properties": {
                    "severity": {"enum": ["high", "medium", "low"]},
                    "claim": {"type": "string"},
                    "trace_refs": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
            },
        },
    }
