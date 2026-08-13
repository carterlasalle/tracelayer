"""SQLite schema migrations and connection management (spec Section 17, §G).

The shared schema is implemented verbatim from the build contract; every
statement is idempotent (``CREATE TABLE IF NOT EXISTS``) and guarded by
``PRAGMA user_version`` so re-opening an existing store is a no-op. FTS5 is
optional: callers that do not need search can open with ``fts=False``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    entity_uid TEXT PRIMARY KEY, trace_id TEXT NOT NULL UNIQUE,
    node_type TEXT NOT NULL, title TEXT, source_kind TEXT NOT NULL,
    canonical_path TEXT, source_start_line INTEGER, source_end_line INTEGER,
    symbol_kind TEXT, symbol_qualified_name TEXT, artifact_fingerprint TEXT,
    revision TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT, last_indexed_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS edges (
    edge_uid TEXT PRIMARY KEY, from_uid TEXT NOT NULL, predicate TEXT NOT NULL,
    to_uid TEXT NOT NULL, source_kind TEXT NOT NULL, source_path TEXT,
    source_line INTEGER, extractor TEXT, confidence REAL NOT NULL DEFAULT 1.0,
    revision TEXT, status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS artifact_versions (
    trace_id TEXT NOT NULL, fingerprint TEXT NOT NULL, revision TEXT,
    observed_at TEXT NOT NULL, source_path TEXT, PRIMARY KEY(trace_id, fingerprint));
CREATE TABLE IF NOT EXISTS verification_bindings (
    evidence_uid TEXT NOT NULL, target_uid TEXT NOT NULL,
    target_fingerprint TEXT, revision TEXT, result TEXT NOT NULL,
    PRIMARY KEY(evidence_uid, target_uid));
CREATE TABLE IF NOT EXISTS evidence_runs (
    run_id TEXT PRIMARY KEY, revision TEXT, provider TEXT, workflow TEXT,
    started_at TEXT, completed_at TEXT, status TEXT NOT NULL, source_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS test_results (
    run_id TEXT NOT NULL, test_uid TEXT, framework_test_id TEXT NOT NULL,
    outcome TEXT NOT NULL, duration_ms REAL, metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(run_id, framework_test_id));
CREATE TABLE IF NOT EXISTS execution_edges (
    run_id TEXT NOT NULL, test_uid TEXT NOT NULL, implementation_uid TEXT NOT NULL,
    coverage_kind TEXT NOT NULL, hit_count INTEGER, confidence REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY(run_id, test_uid, implementation_uid));
CREATE TABLE IF NOT EXISTS diagnostics (
    diagnostic_uid TEXT PRIMARY KEY, rule_id TEXT NOT NULL, severity TEXT NOT NULL,
    trace_id TEXT, path TEXT, line INTEGER, message TEXT NOT NULL, remediation TEXT,
    lifecycle TEXT, revision TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
"""

FTS_TABLE_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(trace_id, title, "
    "symbol_qualified_name, summary, content='')"
)

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edges_from_uid ON edges(from_uid);
CREATE INDEX IF NOT EXISTS idx_edges_to_uid ON edges(to_uid);
CREATE INDEX IF NOT EXISTS idx_nodes_node_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_test_results_framework_test_id ON test_results(framework_test_id);
CREATE INDEX IF NOT EXISTS idx_execution_edges_implementation_uid ON execution_edges(implementation_uid);
"""


def apply_migrations(conn: sqlite3.Connection, *, fts: bool = True) -> None:
    """Create the schema idempotently, guarded by ``PRAGMA user_version``.

    When ``fts`` is enabled the FTS5 index is created if missing, even on a
    store that was previously migrated with ``fts=False``.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(INDEX_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    if fts:
        conn.execute(FTS_TABLE_SQL)
    conn.commit()


def open_connection(path: Path | str, *, fts: bool = True) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and apply migrations.

    ``sqlite3.Row`` rows are enabled so callers can read columns by name.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, fts=fts)
    return conn
