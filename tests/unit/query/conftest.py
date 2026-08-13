"""Local fixtures for tracelayer.query tests (directory scope)."""

from __future__ import annotations

import pytest

from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import entity_uid


@pytest.fixture
def make_node():
    """Factory fixture building a declared Node with deterministic UIDs."""

    def _node(
        trace_id,
        node_type,
        *,
        title=None,
        path=None,
        start=None,
        end=None,
        symbol=None,
        fingerprint=None,
        meta=None,
        status=None,
        active=True,
    ):
        metadata = dict(meta or {})
        if status is not None:
            metadata["status"] = status
        return Node(
            entity_uid=entity_uid(trace_id),
            trace_id=trace_id,
            node_type=node_type,
            source_kind="declared",
            title=title,
            canonical_path=path,
            source_start_line=start,
            source_end_line=end,
            symbol_qualified_name=symbol,
            artifact_fingerprint=fingerprint,
            metadata=metadata,
            last_indexed_at="2026-01-01T00:00:00Z",
            active=active,
        )

    return _node


@pytest.fixture
def make_edge():
    """Factory fixture building an Edge (UID recomputed by the store)."""

    def _edge(frm, predicate, to, *, source_kind="declared", path=None, line=None):
        from_uid = frm if frm.startswith("n_") else entity_uid(frm)
        to_uid = to if to.startswith("n_") else entity_uid(to)
        return Edge(
            edge_uid="",
            from_uid=from_uid,
            predicate=predicate,
            to_uid=to_uid,
            source_kind=source_kind,
            source_path=path,
            source_line=line,
        )

    return _edge
