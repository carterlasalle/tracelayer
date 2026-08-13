"""Evidence ingestion (FR-011, FR-012; spec 25-26).

Combines JUnit / Cobertura / normalized evidence files into one evidence
run, binds outcomes to test uids, and records suite- and per-test
execution edges.  Recoverable input problems (unparseable evidence files,
revision mismatches) become Diagnostics, never exceptions.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tracelayer.config import Project
from tracelayer.diagnostics import Diagnostic, make
from tracelayer.evidence.cobertura import CoberturaParseError, parse_cobertura
from tracelayer.evidence.junit import JUnitParseError, parse_junit
from tracelayer.evidence.models import (
    COVERAGE_SUITE,
    ExecutionRecord,
    NormalizedEvidence,
    TestOutcome,
    entity_uid_for,
)
from tracelayer.evidence.normalized import EvidenceFormatError, parse_normalized
from tracelayer.evidence.per_test import implementation_uid_for
from tracelayer.graph.store import GraphStore
from tracelayer.symbols.base import SymbolRef

# Suite-level execution edges are bound to this sentinel test uid because
# aggregate coverage proves suite execution, not a specific test (spec 17.7).
SUITE_TEST_UID = "suite"


@dataclass
class IngestResult:
    """Summary of one evidence ingest."""

    run_id: str
    tests_ingested: int
    executions_ingested: int
    diagnostics: list[Diagnostic] = field(default_factory=list)


def framework_id_of(symbol: SymbolRef) -> str:
    """Framework id for a test symbol, following the pytest dotted convention.

    The symbols layer builds qualified names as ``path.dots.no_ext.name``
    (e.g. a function ``test_reuse`` in ``tests/auth/test_refresh.py``
    becomes ``tests.auth.test_refresh.test_reuse``), so the framework id is
    the qualified name itself.
    """
    return symbol.qualified_name


def _head_revision(root: Path) -> str | None:
    """Repository HEAD via ``git rev-parse HEAD`` (argv array only).

    Returns None when git is unavailable or the project is not a git repo;
    missing git history is not a trace failure.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _default_run_id(
    revision: str | None,
    provider: str | None,
    workflow: str | None,
    junit: Path | None,
    coverage: Path | None,
    normalized: Path | None,
) -> str:
    """Deterministic, effectively-unique run id (timestamp + content hash)."""
    sources = "|".join(str(p) for p in (junit, coverage, normalized) if p is not None)
    seed = f"{sources}|{revision or ''}|{provider or ''}|{workflow or ''}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"run-{stamp}-{digest}"


def _implementation_uid(store: GraphStore, path: str, start: int, end: int) -> str:
    """Resolve an implementation node uid from canonical path + line range.

    Prefers the first (by entity_uid, for determinism) active
    ``implementation`` node whose source span overlaps [start, end]; falls
    back to the synthetic uid scheme from per_test.py so the edge record is
    still persisted when the node is not indexed.
    """
    for node in sorted(store.all_nodes(), key=lambda n: n.entity_uid):
        if node.node_type != "implementation" or node.canonical_path != path:
            continue
        if node.source_start_line is None or node.source_end_line is None:
            continue
        if node.source_start_line <= end and node.source_end_line >= start:
            return node.entity_uid
    return implementation_uid_for(path)


def ingest(
    project: Project,
    store: GraphStore,
    *,
    junit: Path | None = None,
    coverage: Path | None = None,
    normalized: Path | None = None,
    run_id: str | None = None,
    revision: str | None = None,
    provider: str | None = None,
    workflow: str | None = None,
    test_id_map: dict[str, str] | None = None,
    impl_symbols: dict[str, tuple[int, int]] | None = None,
) -> IngestResult:
    """Ingest evidence files into the graph store as one evidence run.

    Parser failures emit TL051 (with the file path) and do not abort the
    run.  TL050 is emitted when ``config.evidence.require_revision`` is set
    and the evidence revision is missing, or does not match the repository
    HEAD (the "evaluated revision"); in a non-git project only a missing
    revision triggers TL050.  Outcomes are bound to test uids via
    ``test_id_map`` (framework_id -> test trace id); suite-level execution
    edges are emitted for implementation ranges intersected by covered
    lines (coverage_kind="suite", sentinel test uid), per-test edges come
    from the normalized file's ``execution_edges`` verbatim.
    """
    if junit is None and coverage is None and normalized is None:
        raise ValueError("ingest requires at least one of junit=, coverage=, normalized=")
    diags: list[Diagnostic] = []

    outcomes: list[TestOutcome] = []
    coverage_hits: dict[str, list[int]] = {}
    normalized_ev: NormalizedEvidence | None = None
    source_path: str | None = None

    if junit is not None:
        source_path = source_path or str(junit)
        try:
            outcomes.extend(parse_junit(junit))
        except JUnitParseError as exc:
            diags.append(make("TL051", path=str(junit), message=str(exc)))
    if coverage is not None:
        source_path = source_path or str(coverage)
        try:
            coverage_hits = parse_cobertura(coverage)
        except CoberturaParseError as exc:
            diags.append(make("TL051", path=str(coverage), message=str(exc)))
    if normalized is not None:
        source_path = source_path or str(normalized)
        try:
            normalized_ev = parse_normalized(normalized)
        except EvidenceFormatError as exc:
            diags.append(make("TL051", path=str(normalized), message=str(exc)))

    if normalized_ev is not None:
        outcomes.extend(normalized_ev.tests)
        provider = provider or normalized_ev.provider
        workflow = workflow or normalized_ev.workflow
        revision = revision or normalized_ev.revision

    # JUnit and normalized inputs may overlap; keep the first per framework id.
    by_fwid: dict[str, TestOutcome] = {}
    for outcome in outcomes:
        by_fwid.setdefault(outcome.framework_id, outcome)
    outcomes = list(by_fwid.values())

    for outcome in outcomes:
        trace_id = (test_id_map or {}).get(outcome.framework_id)
        if trace_id:
            outcome.test_uid = entity_uid_for(trace_id)

    if normalized_ev is not None:
        status = normalized_ev.status
    else:
        status = "fail" if any(o.outcome in ("fail", "error") for o in outcomes) else "pass"

    run_id = run_id or _default_run_id(revision, provider, workflow, junit, coverage, normalized)
    require_revision = project.config.evidence.require_revision
    if require_revision:
        head = _head_revision(project.root)
        if not revision:
            diags.append(
                make(
                    "TL050",
                    path=source_path,
                    message="evidence revision is required (config evidence.require_revision=true) "
                    "but none was provided",
                    metadata={"evidence_revision": revision, "evaluated_revision": head},
                )
            )
        elif head is not None and revision != head:
            diags.append(
                make(
                    "TL050",
                    path=source_path,
                    message=(
                        f"evidence revision {revision} does not match evaluated revision {head}"
                    ),
                    metadata={"evidence_revision": revision, "evaluated_revision": head},
                )
            )

    store.add_evidence_run(
        run_id,
        revision,
        provider,
        workflow,
        normalized_ev.started_at if normalized_ev else None,
        normalized_ev.completed_at if normalized_ev else None,
        status,
        source_path,
        metadata={
            "sources": [str(p) for p in (junit, coverage, normalized) if p is not None],
            "require_revision": require_revision,
        },
    )
    store.add_test_results(run_id, outcomes)

    records: list[ExecutionRecord] = []
    if coverage_hits and impl_symbols:
        for path, (start, end) in sorted(impl_symbols.items()):
            hit_lines = coverage_hits.get(path) or []
            hit_count = sum(1 for line in hit_lines if start <= line <= end)
            if hit_count == 0:
                continue
            records.append(
                ExecutionRecord(
                    run_id=run_id,
                    test_uid=SUITE_TEST_UID,
                    implementation_uid=_implementation_uid(store, path, start, end),
                    coverage_kind=COVERAGE_SUITE,
                    hit_count=hit_count,
                )
            )
    if normalized_ev is not None:
        for rec in normalized_ev.execution_edges:
            records.append(
                ExecutionRecord(
                    run_id=run_id,
                    test_uid=rec.test_uid,
                    implementation_uid=rec.implementation_uid,
                    coverage_kind=rec.coverage_kind,
                    hit_count=rec.hit_count,
                    confidence=rec.confidence,
                    metadata=rec.metadata,
                )
            )
    store.add_execution_edges(run_id, records)

    return IngestResult(
        run_id=run_id,
        tests_ingested=len(outcomes),
        executions_ingested=len(records),
        diagnostics=diags,
    )
