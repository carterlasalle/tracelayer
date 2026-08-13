"""GraphStore: SQLite-backed materialized trace graph (spec §G).

Persistence layer for nodes, edges, diagnostics, evidence, and FTS5 search.
All JSON columns are serialized with ``json.dumps``; loaders rebuild the
Node/Edge/Diagnostic dataclasses. UIDs are recomputed from the deterministic
scheme in the build contract so the store is canonical regardless of what
callers pass in.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from tracelayer.diagnostics import Diagnostic
from tracelayer.evidence.models import ExecutionRecord, TestOutcome
from tracelayer.graph.migrations import FTS_TABLE_SQL, open_connection
from tracelayer.graph.models import Edge, Node


def _hash(*parts: object) -> str:
    """sha256 of parts joined with ``|``; ``None`` renders as the empty string."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def entity_uid(trace_id: str) -> str:
    """Deterministic node UID: ``n_`` + sha256(trace_id)[:32]."""
    return "n_" + _hash(trace_id)


def edge_uid(
    from_uid: str,
    predicate: str,
    to_uid: str,
    source_kind: str,
    source_path: str | None,
    source_line: int | None,
) -> str:
    """Deterministic edge UID from the shared scheme."""
    return "e_" + _hash(from_uid, predicate, to_uid, source_kind, source_path, source_line)


def diagnostic_uid(
    rule_id: str,
    trace_id: str | None,
    path: str | None,
    line: int | None,
    message: str,
) -> str:
    """Deterministic diagnostic UID: ``d_`` + sha256(rule|trace|path|line|msg)[:32]."""
    return "d_" + _hash(rule_id, trace_id, path, line, message)


def _fts_summary(node: Node) -> str:
    """FTS summary column: metadata summary plus work label (spec 17.9)."""
    parts = [node.metadata.get("summary"), node.metadata.get("work_label")]
    return "\n".join(p for p in parts if p)


class GraphStore:
    """Materialized graph store backed by one SQLite database (WAL mode)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._fts = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
            ).fetchone()
            is not None
        )
        # In-session overlay: execution_edges has no metadata column in the
        # shared schema, so ExecutionRecord metadata lives only for the life of
        # this store (level-3 behavioral proof, spec 25.2).
        self._exec_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}

    @classmethod
    def open(cls, path: Path | str, *, fts: bool = True) -> GraphStore:
        """Open (creating if needed) the store at ``path``."""
        return cls(open_connection(path, fts=fts))

    def close(self) -> None:
        """Close the underlying SQLite connection and drop in-session state."""
        self._exec_metadata.clear()
        self._conn.close()

    # ------------------------------------------------------------- rebuild

    def replace_all(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Atomically rebuild the graph in one transaction.

        Wipes nodes, edges, diagnostics, and the FTS index, then inserts the
        given batch. Duplicate rows collapse via ``INSERT OR REPLACE`` (last
        wins). Node/edge UIDs are recomputed from the deterministic scheme.
        """
        with self._conn:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM diagnostics")
            if self._fts:
                # Contentless FTS5 tables reject DELETE; rebuild the index.
                self._conn.execute("DROP TABLE IF EXISTS nodes_fts")
                self._conn.execute(FTS_TABLE_SQL)
            for node in nodes:
                cur = self._conn.execute(
                    "INSERT OR REPLACE INTO nodes (entity_uid, trace_id, node_type, title, "
                    "source_kind, canonical_path, source_start_line, source_end_line, "
                    "symbol_kind, symbol_qualified_name, artifact_fingerprint, revision, "
                    "metadata_json, first_seen_at, last_indexed_at, active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entity_uid(node.trace_id),
                        node.trace_id,
                        node.node_type,
                        node.title,
                        node.source_kind,
                        node.canonical_path,
                        node.source_start_line,
                        node.source_end_line,
                        node.symbol_kind,
                        node.symbol_qualified_name,
                        node.artifact_fingerprint,
                        node.revision,
                        json.dumps(node.metadata),
                        node.first_seen_at,
                        node.last_indexed_at,
                        int(node.active),
                    ),
                )
                if self._fts:
                    self._conn.execute(
                        "INSERT INTO nodes_fts (rowid, trace_id, title, "
                        "symbol_qualified_name, summary) VALUES (?,?,?,?,?)",
                        (
                            cur.lastrowid,
                            node.trace_id,
                            node.title or "",
                            node.symbol_qualified_name or "",
                            _fts_summary(node),
                        ),
                    )
            for edge in edges:
                self._conn.execute(
                    "INSERT OR REPLACE INTO edges (edge_uid, from_uid, predicate, to_uid, "
                    "source_kind, source_path, source_line, extractor, confidence, revision, "
                    "status, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        edge_uid(
                            edge.from_uid,
                            edge.predicate,
                            edge.to_uid,
                            edge.source_kind,
                            edge.source_path,
                            edge.source_line,
                        ),
                        edge.from_uid,
                        edge.predicate,
                        edge.to_uid,
                        edge.source_kind,
                        edge.source_path,
                        edge.source_line,
                        edge.extractor,
                        edge.confidence,
                        edge.revision,
                        edge.status,
                        json.dumps(edge.metadata),
                    ),
                )

    # --------------------------------------------------------- diagnostics

    def insert_diagnostics(self, diags: list[Diagnostic]) -> None:
        """Insert diagnostics (deduplicated by deterministic UID)."""
        with self._conn:
            for d in diags:
                self._insert_diagnostic(d)

    def replace_diagnostics(self, diags: list[Diagnostic]) -> None:
        """Wipe and re-insert all diagnostics in one transaction."""
        with self._conn:
            self._conn.execute("DELETE FROM diagnostics")
            for d in diags:
                self._insert_diagnostic(d)

    def _insert_diagnostic(self, d: Diagnostic) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO diagnostics (diagnostic_uid, rule_id, severity, "
            "trace_id, path, line, message, remediation, lifecycle, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                diagnostic_uid(d.rule_id, d.trace_id, d.path, d.line, d.message),
                d.rule_id,
                d.severity,
                d.trace_id,
                d.path,
                d.line,
                d.message,
                d.remediation,
                d.lifecycle,
                json.dumps(d.metadata),
            ),
        )

    def get_diagnostics(
        self, *, severity: str | None = None, rule_id: str | None = None
    ) -> list[Diagnostic]:
        """Return diagnostics, optionally filtered, in insertion order."""
        sql = "SELECT * FROM diagnostics"
        conds: list[str] = []
        params: list[Any] = []
        if severity is not None:
            conds.append("severity = ?")
            params.append(severity)
        if rule_id is not None:
            conds.append("rule_id = ?")
            params.append(rule_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY rowid"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_diagnostic(r) for r in rows]

    # ----------------------------------------------------------------- nodes

    def get_node(self, uid: str | None = None, trace_id: str | None = None) -> Node | None:
        """Fetch one node by entity uid or trace id (exactly one must be given)."""
        if (uid is None) == (trace_id is None):
            raise ValueError("get_node requires exactly one of uid or trace_id")
        if uid is not None:
            row = self._conn.execute("SELECT * FROM nodes WHERE entity_uid = ?", (uid,)).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        return None if row is None else self._row_to_node(row)

    def get_node_uid(self, trace_id: str) -> str | None:
        """Resolve a trace id to its entity uid, or None."""
        row = self._conn.execute(
            "SELECT entity_uid FROM nodes WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return None if row is None else row["entity_uid"]

    def all_nodes(self, *, active_only: bool = True) -> list[Node]:
        """All nodes ordered by entity uid."""
        sql = "SELECT * FROM nodes"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY entity_uid"
        return [self._row_to_node(r) for r in self._conn.execute(sql).fetchall()]

    def trace_id_exists(self, trace_id: str) -> bool:
        return self.get_node_uid(trace_id) is not None

    def mark_inactive_except(self, active_uids: set[str]) -> None:
        """Mark nodes not in ``active_uids`` inactive (spec 18.3)."""
        if not active_uids:
            self._conn.execute("UPDATE nodes SET active = 0")
        else:
            marks = ",".join("?" * len(active_uids))
            # Only "?" placeholders are interpolated; values are bound params.
            self._conn.execute(
                f"UPDATE nodes SET active = 0 WHERE entity_uid NOT IN ({marks})",  # nosec B608
                sorted(active_uids),
            )
        self._conn.commit()

    def set_node_meta(self, trace_id: str, key: str, value: Any) -> None:
        """Merge ``key: value`` into a node's metadata (no-op if absent)."""
        row = self._conn.execute(
            "SELECT metadata_json FROM nodes WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if row is None:
            return
        meta = json.loads(row["metadata_json"] or "{}")
        meta[key] = value
        self._conn.execute(
            "UPDATE nodes SET metadata_json = ? WHERE trace_id = ?",
            (json.dumps(meta), trace_id),
        )
        self._conn.commit()

    def set_node_fingerprint(self, trace_id: str, fingerprint: str) -> None:
        """Record the current artifact fingerprint of a node."""
        self._conn.execute(
            "UPDATE nodes SET artifact_fingerprint = ? WHERE trace_id = ?",
            (fingerprint, trace_id),
        )
        self._conn.commit()

    # ----------------------------------------------------------------- edges

    def all_edges(self, *, status: str | None = None) -> list[Edge]:
        """All edges in insertion order, optionally filtered by status."""
        if status is None:
            rows = self._conn.execute("SELECT * FROM edges ORDER BY rowid").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE status = ? ORDER BY rowid", (status,)
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def edges_from(self, uid: str, predicate: str | None = None) -> list[Edge]:
        """Outgoing edges of ``uid`` in insertion order, optional predicate."""
        if predicate is None:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE from_uid = ? ORDER BY rowid", (uid,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE from_uid = ? AND predicate = ? ORDER BY rowid",
                (uid, predicate),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def edges_to(self, uid: str, predicate: str | None = None) -> list[Edge]:
        """Incoming edges of ``uid`` in insertion order, optional predicate."""
        if predicate is None:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE to_uid = ? ORDER BY rowid", (uid,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE to_uid = ? AND predicate = ? ORDER BY rowid",
                (uid, predicate),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def set_edge_status(self, edge_uid: str, status: str) -> None:
        """Set one edge's status."""
        self._conn.execute("UPDATE edges SET status = ? WHERE edge_uid = ?", (status, edge_uid))
        self._conn.commit()

    def set_edge_statuses_for_node(self, uid: str, status: str) -> None:
        """Set status for every edge touching ``uid`` (either endpoint)."""
        self._conn.execute(
            "UPDATE edges SET status = ? WHERE from_uid = ? OR to_uid = ?",
            (status, uid, uid),
        )
        self._conn.commit()

    # ----------------------------------------------------- artifact versions

    def record_artifact_version(
        self,
        trace_id: str,
        fingerprint: str,
        revision: str | None,
        source_path: str | None,
        observed_at: str,
    ) -> None:
        """Record a fingerprint observation for staleness tracking."""
        self._conn.execute(
            "INSERT OR REPLACE INTO artifact_versions (trace_id, fingerprint, revision, "
            "observed_at, source_path) VALUES (?,?,?,?,?)",
            (trace_id, fingerprint, revision, source_path, observed_at),
        )
        self._conn.commit()

    def previous_fingerprints(self, trace_id: str, *, exclude: str | None = None) -> list[str]:
        """Recorded fingerprints oldest first (newest last), optionally excluding one."""
        sql = "SELECT fingerprint FROM artifact_versions WHERE trace_id = ?"
        params: list[Any] = [trace_id]
        if exclude is not None:
            sql += " AND fingerprint != ?"
            params.append(exclude)
        sql += " ORDER BY observed_at, fingerprint"
        return [r["fingerprint"] for r in self._conn.execute(sql, params).fetchall()]

    def current_fingerprint(self, trace_id: str) -> str | None:
        """The node's current artifact fingerprint, or None."""
        row = self._conn.execute(
            "SELECT artifact_fingerprint FROM nodes WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return None if row is None else row["artifact_fingerprint"]

    # --------------------------------------------------------------- evidence

    def add_evidence_run(
        self,
        run_id: str,
        revision: str | None,
        provider: str | None,
        workflow: str | None,
        started_at: str | None,
        completed_at: str | None,
        status: str,
        source_path: str | None,
        metadata: dict[str, Any],
    ) -> None:
        """Insert or replace one evidence run."""
        self._conn.execute(
            "INSERT OR REPLACE INTO evidence_runs (run_id, revision, provider, workflow, "
            "started_at, completed_at, status, source_path, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                revision,
                provider,
                workflow,
                started_at,
                completed_at,
                status,
                source_path,
                json.dumps(metadata),
            ),
        )
        self._conn.commit()

    def latest_evidence_run(self, revision: str | None = None) -> dict | None:
        """Most recent evidence run (optionally for one revision) as a row dict."""
        if revision is None:
            row = self._conn.execute(
                "SELECT * FROM evidence_runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM evidence_runs WHERE revision = ? "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1",
                (revision,),
            ).fetchone()
        return None if row is None else dict(row)

    def get_evidence_runs(self, revision: str | None = None) -> list[dict]:
        """Evidence runs, newest first, as row dicts (optional revision filter)."""
        if revision is None:
            rows = self._conn.execute(
                "SELECT * FROM evidence_runs ORDER BY started_at DESC, run_id DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM evidence_runs WHERE revision = ? "
                "ORDER BY started_at DESC, run_id DESC",
                (revision,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_test_results(self, run_id: str, outcomes: list[TestOutcome]) -> None:
        """Insert or replace test outcomes for one run."""
        with self._conn:
            for o in outcomes:
                self._conn.execute(
                    "INSERT OR REPLACE INTO test_results (run_id, test_uid, "
                    "framework_test_id, outcome, duration_ms, metadata_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        run_id,
                        o.test_uid,
                        o.framework_id,
                        o.outcome,
                        o.duration_ms,
                        json.dumps(o.metadata),
                    ),
                )

    def outcomes_for_run(self, run_id: str) -> list[TestOutcome]:
        """Test outcomes for one run, ordered by framework test id."""
        rows = self._conn.execute(
            "SELECT * FROM test_results WHERE run_id = ? ORDER BY framework_test_id",
            (run_id,),
        ).fetchall()
        return [self._row_to_outcome(r) for r in rows]

    def latest_outcome(self, framework_test_id: str) -> TestOutcome | None:
        """Most recent outcome for a test across runs (by run start time)."""
        row = self._conn.execute(
            "SELECT t.* FROM test_results t "
            "LEFT JOIN evidence_runs r ON r.run_id = t.run_id "
            "WHERE t.framework_test_id = ? "
            "ORDER BY COALESCE(r.started_at, t.run_id) DESC, t.run_id DESC LIMIT 1",
            (framework_test_id,),
        ).fetchone()
        return None if row is None else self._row_to_outcome(row)

    def add_execution_edges(self, run_id: str, records: list[ExecutionRecord]) -> None:
        """Insert or replace execution records for one run.

        The ``run_id`` argument is authoritative; records carry their own run
        id for reconstruction on read. ``ExecutionRecord.metadata`` has no
        column in the shared ``execution_edges`` schema, so it is kept in an
        in-session overlay and merged back into reconstructed records (cleared
        on close; proof_level degrades to level 2 across sessions).
        """
        with self._conn:
            for rec in records:
                key = (run_id, rec.test_uid, rec.implementation_uid)
                if rec.metadata:
                    self._exec_metadata[key] = dict(rec.metadata)
                else:
                    self._exec_metadata.pop(key, None)
                self._conn.execute(
                    "INSERT OR REPLACE INTO execution_edges (run_id, test_uid, "
                    "implementation_uid, coverage_kind, hit_count, confidence) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        run_id,
                        rec.test_uid,
                        rec.implementation_uid,
                        rec.coverage_kind,
                        rec.hit_count,
                        rec.confidence,
                    ),
                )

    def execution_edges_for(self, implementation_uid: str) -> list[ExecutionRecord]:
        """Execution records touching an implementation, ordered by run/test."""
        rows = self._conn.execute(
            "SELECT * FROM execution_edges WHERE implementation_uid = ? ORDER BY run_id, test_uid",
            (implementation_uid,),
        ).fetchall()
        out = []
        for r in rows:
            rec = self._row_to_execution(r)
            rec.metadata = self._merged_exec_metadata(rec)
            out.append(rec)
        return out

    def execution_edges_for_test(self, test_uid: str) -> list[ExecutionRecord]:
        """Execution records touching a test, ordered by run/implementation."""
        rows = self._conn.execute(
            "SELECT * FROM execution_edges WHERE test_uid = ? ORDER BY run_id, implementation_uid",
            (test_uid,),
        ).fetchall()
        out = []
        for r in rows:
            rec = self._row_to_execution(r)
            rec.metadata = self._merged_exec_metadata(rec)
            out.append(rec)
        return out

    def add_verification_binding(
        self,
        evidence_uid: str,
        target_uid: str,
        target_fingerprint: str | None,
        revision: str | None,
        result: str,
    ) -> None:
        """Bind an evidence row to a target fingerprint."""
        self._conn.execute(
            "INSERT OR REPLACE INTO verification_bindings (evidence_uid, target_uid, "
            "target_fingerprint, revision, result) VALUES (?,?,?,?,?)",
            (evidence_uid, target_uid, target_fingerprint, revision, result),
        )
        self._conn.commit()

    def bindings_for(self, target_uid: str) -> list[dict]:
        """Verification bindings for a target as row dicts."""
        rows = self._conn.execute(
            "SELECT * FROM verification_bindings WHERE target_uid = ?",
            (target_uid,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------- search + stats

    def search(self, query: str, limit: int = 20) -> list[Node]:
        """Search nodes by trace id, title, symbol, summary, or work label.

        Prefers FTS5 (relevance-ranked); falls back to a LIKE scan for
        queries FTS5 rejects (e.g. ``TR:123`` column-filter syntax) or when
        the store was opened without FTS.
        """
        if not query.strip() or limit < 1:
            return []
        if self._fts:
            try:
                rows = self._conn.execute(
                    "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
                    "WHERE nodes_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, limit),
                ).fetchall()
                if rows:
                    return [self._row_to_node(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # malformed FTS query -> LIKE fallback
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{esc}%"
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE trace_id LIKE ? ESCAPE '\\' "
            "OR title LIKE ? ESCAPE '\\' OR symbol_qualified_name LIKE ? ESCAPE '\\' "
            "OR json_extract(metadata_json, '$.summary') LIKE ? ESCAPE '\\' "
            "OR json_extract(metadata_json, '$.work_label') LIKE ? ESCAPE '\\' "
            "ORDER BY trace_id LIMIT ?",
            (pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def stats(self) -> dict:
        """Aggregate counts; ``changed_artifacts`` counts nodes flagged in
        metadata (``metadata['changed']`` truthy) or in a staleness status."""
        c = self._conn

        def count(sql: str) -> int:
            return c.execute(sql).fetchone()[0]

        return {
            "nodes": count("SELECT COUNT(*) FROM nodes"),
            "declared_edges": count("SELECT COUNT(*) FROM edges WHERE source_kind = 'declared'"),
            "structural_edges": count(
                "SELECT COUNT(*) FROM edges WHERE source_kind = 'structural'"
            ),
            "observed_edges": count("SELECT COUNT(*) FROM edges WHERE source_kind = 'observed'"),
            "evidence_runs": count("SELECT COUNT(*) FROM evidence_runs"),
            "diagnostics": count("SELECT COUNT(*) FROM diagnostics"),
            "changed_artifacts": count(
                "SELECT COUNT(*) FROM nodes WHERE "
                "json_extract(metadata_json, '$.changed') = 1 OR "
                "json_extract(metadata_json, '$.status') IN "
                "('stale_review_required', 'reviewed_needs_verification')"
            ),
        }

    # -------------------------------------------------------------- row mappers

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        return Node(
            entity_uid=row["entity_uid"],
            trace_id=row["trace_id"],
            node_type=row["node_type"],
            source_kind=row["source_kind"],
            title=row["title"],
            canonical_path=row["canonical_path"],
            source_start_line=row["source_start_line"],
            source_end_line=row["source_end_line"],
            symbol_kind=row["symbol_kind"],
            symbol_qualified_name=row["symbol_qualified_name"],
            artifact_fingerprint=row["artifact_fingerprint"],
            revision=row["revision"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            first_seen_at=row["first_seen_at"],
            last_indexed_at=row["last_indexed_at"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> Edge:
        return Edge(
            edge_uid=row["edge_uid"],
            from_uid=row["from_uid"],
            predicate=row["predicate"],
            to_uid=row["to_uid"],
            source_kind=row["source_kind"],
            source_path=row["source_path"],
            source_line=row["source_line"],
            extractor=row["extractor"],
            confidence=row["confidence"],
            revision=row["revision"],
            status=row["status"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_diagnostic(row: sqlite3.Row) -> Diagnostic:
        return Diagnostic(
            rule_id=row["rule_id"],
            severity=row["severity"],
            message=row["message"],
            trace_id=row["trace_id"],
            path=row["path"],
            line=row["line"],
            remediation=row["remediation"],
            lifecycle=row["lifecycle"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_outcome(row: sqlite3.Row) -> TestOutcome:
        return TestOutcome(
            framework_id=row["framework_test_id"],
            outcome=row["outcome"],
            duration_ms=row["duration_ms"],
            test_uid=row["test_uid"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> ExecutionRecord:
        return ExecutionRecord(
            run_id=row["run_id"],
            test_uid=row["test_uid"],
            implementation_uid=row["implementation_uid"],
            coverage_kind=row["coverage_kind"],
            hit_count=row["hit_count"],
            confidence=row["confidence"],
        )

    def _merged_exec_metadata(self, rec: ExecutionRecord) -> dict[str, Any]:
        """Record metadata plus the in-session overlay (level-3 proof)."""
        meta = dict(rec.metadata)
        extra = self._exec_metadata.get((rec.run_id, rec.test_uid, rec.implementation_uid))
        if extra:
            meta.update(extra)
        return meta
