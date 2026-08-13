"""Canonical node/edge domain models (spec Section 17).

The SQLite database is a materialized index; these dataclasses are the
in-memory representation shared by the indexer, policy engine, and queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOURCE_KINDS = ("declared", "structural", "observed", "imported", "suggested")

NODE_STATUSES = (
    "current",
    "stale_review_required",
    "reviewed_needs_verification",
    "retired",
)

EDGE_STATUSES = (
    "active",
    "stale_review_required",
    "reviewed_needs_verification",
    "historical",
    "retired",
)

# Evidence status for historical-but-not-current evidence (FR-009).
EVIDENCE_STATUS_HISTORICAL = "historical_not_current"


@dataclass
class Node:
    entity_uid: str
    trace_id: str
    node_type: str
    source_kind: str
    title: str | None = None
    canonical_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    symbol_kind: str | None = None
    symbol_qualified_name: str | None = None
    artifact_fingerprint: str | None = None
    revision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    first_seen_at: str | None = None
    last_indexed_at: str | None = None
    active: bool = True

    def status(self) -> str:
        """Node-level staleness state; defaults to current."""
        return str(self.metadata.get("status", "current"))


@dataclass
class Edge:
    edge_uid: str
    from_uid: str
    predicate: str
    to_uid: str
    source_kind: str
    source_path: str | None = None
    source_line: int | None = None
    extractor: str | None = None
    confidence: float = 1.0
    revision: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
