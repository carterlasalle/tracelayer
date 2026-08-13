"""Per-test coverage adapter (spec 17.7, 25.2 level 2).

Reference L2 adapter for coverage sqlite databases collected with
``pytest-cov --cov-context=test``.  Requires the ``coverage`` dev
dependency (import-guarded: RuntimeError when missing).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

from tracelayer.evidence.models import COVERAGE_PER_TEST, ExecutionRecord, entity_uid_for


def implementation_uid_for(canonical_path: str) -> str:
    """Stable synthetic entity uid derived from a canonical path.

    Used when the graph store cannot resolve the implementation node;
    ingest prefers the real node uid (looked up by canonical path and line
    range) and falls back to this scheme so records stay non-null.
    """
    return "n_" + hashlib.sha256(f"impl:{canonical_path}".encode()).hexdigest()[:32]


def _context_to_framework_id(context: str) -> str:
    """pytest nodeid -> dotted framework id.

    ``tests/auth/test_refresh.py::test_reuse[param]`` becomes
    ``tests.auth.test_refresh.test_reuse``: drop the parametrize suffix,
    turn ``::`` into ``.``, strip the ``.py`` module extension (which sits
    right before the test name after the ``::`` conversion), then join path
    segments with ``.`` — the convention used by ``framework_id_of``.
    """
    node = context.split("[", 1)[0]
    node = node.replace("::", ".")
    node = node.replace(".py.", ".")
    return node.replace("/", ".")


def collect_pytest_per_test(
    coverage_db: str,
    impl_symbols: dict[str, tuple[int, int]],
    test_id_map: dict[str, str],
) -> list[ExecutionRecord]:
    """Build per-test execution records from a coverage sqlite database.

    Each pytest context (test nodeid) is normalized to a framework id and
    mapped through ``test_id_map`` (framework_id -> test trace id) to a
    test_uid; executed lines are intersected with ``impl_symbols`` ranges
    (canonical path -> (start, end) inclusive).  ``run_id`` is left empty
    for the caller to assign.  Contexts and files without a mapped test are
    skipped — aggregate coverage without contexts yields no records, which
    is the honest answer (suite coverage cannot produce per-test proof).
    ``hit_count`` is the number of (line, context) executions observed in
    the implementation range; parametrized variants of one test all map to
    the same framework id and are summed together.
    """
    try:
        from coverage import CoverageData
    except ImportError as exc:
        raise RuntimeError("coverage not installed") from exc
    if not os.path.exists(coverage_db):
        raise RuntimeError(f"coverage database not found: {coverage_db}")
    data = CoverageData(basename=coverage_db)
    try:
        data.read()
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        raise RuntimeError(f"cannot read coverage database {coverage_db}: {exc}") from exc

    context_tests: dict[str, str] = {}
    for ctx in data.measured_contexts():
        trace_id = test_id_map.get(_context_to_framework_id(ctx))
        if trace_id:
            context_tests[ctx] = entity_uid_for(trace_id)

    hits: dict[tuple[str, str], int] = {}
    files = set(data.measured_files()) & set(impl_symbols)
    for filename in sorted(files):
        start, end = impl_symbols[filename]
        by_line = data.contexts_by_lineno(filename) or {}
        for line, contexts in by_line.items():
            if not (start <= line <= end):
                continue
            for ctx in contexts:
                test_uid = context_tests.get(ctx)
                if test_uid is None:
                    continue
                key = (test_uid, implementation_uid_for(filename))
                hits[key] = hits.get(key, 0) + 1

    return [
        ExecutionRecord(
            run_id="",
            test_uid=test_uid,
            implementation_uid=impl_uid,
            coverage_kind=COVERAGE_PER_TEST,
            hit_count=count,
        )
        for (test_uid, impl_uid), count in sorted(hits.items())
    ]
