"""Evidence domain models (spec 25, FR-011).

Pure dataclasses shared by the evidence parsers, the graph store (§G) and
ingest.  This module must not import graph modules: the store imports from
here, and importing it back would create a cycle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

EVIDENCE_SCHEMA = "tracelayer-evidence/v1"

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_SKIP = "skip"
OUTCOME_ERROR = "error"
OUTCOMES = (OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_SKIP, OUTCOME_ERROR)

COVERAGE_SUITE = "suite"
COVERAGE_PER_TEST = "per_test"
COVERAGE_KINDS = (COVERAGE_SUITE, COVERAGE_PER_TEST)


def entity_uid_for(trace_id: str) -> str:
    """Deterministic node entity_uid for a trace id (contract UID scheme).

    `"n_" + sha256(trace_id)[:32]` — identical to the graph store's scheme,
    so callers can bind evidence to nodes that may not be indexed yet.
    """
    return "n_" + hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:32]


@dataclass
class TestOutcome:
    """Outcome of a single test in one evidence run."""

    framework_id: str
    outcome: str  # pass | fail | skip | error
    duration_ms: float | None = None
    test_uid: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionRecord:
    """Observed execution of an implementation by a test (FR-012)."""

    run_id: str
    test_uid: str
    implementation_uid: str
    coverage_kind: str  # suite | per_test
    hit_count: int | None = None
    confidence: float = 1.0
    # Level-3 marker (spec 25.2): metadata["behavioral"] == True means the
    # edge carries richer behavioral evidence.  The execution_edges SQL
    # table has no metadata column, so a GraphStore round-trip drops it;
    # proof_level degrades to level 2 then (documented in freshness.py).
    metadata: dict = field(default_factory=dict)


@dataclass
class NormalizedEvidence:
    """A parsed tracelayer-evidence/v1 record (spec 25.1)."""

    schema: str  # must be "tracelayer-evidence/v1"
    run_id: str
    revision: str | None
    provider: str | None
    workflow: str | None
    started_at: str | None
    completed_at: str | None
    status: str  # pass | fail
    tests: list[TestOutcome]
    execution_edges: list[ExecutionRecord]
    metadata: dict = field(default_factory=dict)
