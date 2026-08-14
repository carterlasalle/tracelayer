"""Shared fixtures for tests/unit (owned by TestsPolicyEvidence).

Self-contained: does not depend on tests/conftest.py so the policy/evidence
unit trees run in isolation.  A `Project` + fresh `GraphStore` in tmp_path
with no git repository and no network is all these tests need; deterministic
revisions are passed explicitly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tracelayer.config import PolicyConfig, Project, TraceConfig
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A Project rooted in tmp_path with default trace config and an empty policy."""
    (tmp_path / ".trace").mkdir()
    config = TraceConfig(repo_id="test-repo", cache_dir=".trace/cache")
    return Project(root=tmp_path, config=config, policy=PolicyConfig())


@pytest.fixture
def store(project: Project) -> Iterator[GraphStore]:
    """A fresh GraphStore under the project cache dir; closed on teardown."""
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    s = GraphStore.open(project.db_path)
    yield s
    s.close()


# trace:v1 id=test.dogfood.tests.unit.conftest.py type=test
def make_node(
    trace_id: str,
    node_type: str,
    *,
    path: str | None = None,
    status: str | None = None,
    start: int | None = None,
    end: int | None = None,
    active: bool = True,
    fingerprint: str | None = None,
    framework_test_id: str | None = None,
    metadata: dict | None = None,
) -> Node:
    """Build a Node with the deterministic entity_uid scheme (contract §G)."""
    import hashlib

    meta = dict(metadata or {})
    if status is not None:
        meta["status"] = status
    if framework_test_id is not None:
        meta["framework_test_id"] = framework_test_id
    return Node(
        entity_uid="n_" + hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:32],
        trace_id=trace_id,
        node_type=node_type,
        source_kind="declared",
        title=trace_id,
        canonical_path=path,
        source_start_line=start,
        source_end_line=end,
        artifact_fingerprint=fingerprint,
        metadata=meta,
        active=active,
        last_indexed_at="2026-01-01T00:00:00Z",
    )


def make_edge(
    from_uid: str,
    predicate: str,
    to_uid: str,
    *,
    source_path: str | None = None,
    source_line: int | None = None,
    status: str = "active",
) -> Edge:
    """Build an Edge; edge_uid is recomputed by the store on insert."""
    return Edge(
        edge_uid="",
        from_uid=from_uid,
        predicate=predicate,
        to_uid=to_uid,
        source_kind="declared",
        source_path=source_path,
        source_line=source_line,
        status=status,
    )
