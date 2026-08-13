"""Normalized trace evidence JSON parser (spec 25.1, FR-011).

The canonical machine format is ``tracelayer-evidence/v1``; anything else
is rejected with EvidenceFormatError (a ValueError), which the ingest layer
converts into a TL051 diagnostic.  Evidence files are untrusted input.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracelayer.evidence.models import (
    COVERAGE_KINDS,
    EVIDENCE_SCHEMA,
    OUTCOMES,
    ExecutionRecord,
    NormalizedEvidence,
    TestOutcome,
    entity_uid_for,
)


class EvidenceFormatError(ValueError):
    """Raised when a normalized evidence file is malformed."""


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _opt_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def parse_normalized(path: Path) -> NormalizedEvidence:
    """Parse a tracelayer-evidence/v1 JSON file into NormalizedEvidence.

    Validates the schema marker and the required ``run_id``/``status``
    fields; unknown extra keys are ignored for forward compatibility.  An
    optional per-test ``trace_id`` is bound to ``test_uid`` via the
    deterministic entity uid scheme.  Per-test ``execution_edges`` are
    preserved verbatim (including their metadata, so level-3 behavioral
    evidence survives ingestion).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceFormatError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceFormatError(f"{path}: top-level JSON value must be an object")
    if data.get("schema") != EVIDENCE_SCHEMA:
        raise EvidenceFormatError(
            f"{path}: schema is {data.get('schema')!r}, expected {EVIDENCE_SCHEMA!r}"
        )
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise EvidenceFormatError(f"{path}: missing or invalid run_id")
    status = data.get("status")
    if status not in ("pass", "fail"):
        raise EvidenceFormatError(f"{path}: status must be 'pass' or 'fail', got {status!r}")

    tests: list[TestOutcome] = []
    for item in data.get("tests") or []:
        if not isinstance(item, dict):
            raise EvidenceFormatError(f"{path}: test entry must be an object")
        framework_id = item.get("framework_id")
        outcome = item.get("outcome")
        if not isinstance(framework_id, str) or not framework_id or outcome not in OUTCOMES:
            raise EvidenceFormatError(
                f"{path}: test entry requires a non-empty framework_id and a valid outcome"
            )
        trace_id = item.get("trace_id")
        tests.append(
            TestOutcome(
                framework_id=framework_id,
                outcome=outcome,
                duration_ms=_opt_float(item.get("duration_ms")),
                test_uid=(
                    entity_uid_for(trace_id) if isinstance(trace_id, str) and trace_id else None
                ),
            )
        )

    edges: list[ExecutionRecord] = []
    for item in data.get("execution_edges") or []:
        if not isinstance(item, dict):
            raise EvidenceFormatError(f"{path}: execution edge entry must be an object")
        test_uid = item.get("test_uid")
        impl_uid = item.get("implementation_uid")
        kind = item.get("coverage_kind")
        if not isinstance(test_uid, str) or not test_uid:
            raise EvidenceFormatError(f"{path}: execution edge requires a non-empty test_uid")
        if not isinstance(impl_uid, str) or not impl_uid:
            raise EvidenceFormatError(
                f"{path}: execution edge requires a non-empty implementation_uid"
            )
        if kind not in COVERAGE_KINDS:
            raise EvidenceFormatError(
                f"{path}: execution edge coverage_kind must be one of {COVERAGE_KINDS}, "
                f"got {kind!r}"
            )
        edges.append(
            ExecutionRecord(
                run_id=run_id,
                test_uid=test_uid,
                implementation_uid=impl_uid,
                coverage_kind=kind,
                hit_count=_opt_int(item.get("hit_count")),
                confidence=float(item.get("confidence", 1.0)),
                metadata=dict(item.get("metadata") or {}),
            )
        )

    return NormalizedEvidence(
        schema=EVIDENCE_SCHEMA,
        run_id=run_id,
        revision=_opt_str(data.get("revision")),
        provider=_opt_str(data.get("provider")),
        workflow=_opt_str(data.get("workflow")),
        started_at=_opt_str(data.get("started_at")),
        completed_at=_opt_str(data.get("completed_at")),
        status=status,
        tests=tests,
        execution_edges=edges,
        metadata=dict(data.get("metadata") or {}),
    )
