"""Evidence ingestion (contract §E): suite-level execution edges, test_id_map
resolution, revision binding (TL050), and framework_id_of.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.conftest import make_node
from tracelayer.evidence.ingest import framework_id_of, ingest
from tracelayer.symbols.base import SymbolRef


# trace:v1 id=test.dogfood.tests.unit.evidence.test_ingest.py type=test
def write_file(root, name: str, content: str):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def impl_graph(store):
    """Two implementation nodes with disjoint line ranges in the same file."""
    impl_a = make_node("IMPL:A", "implementation", path="src/app.py", start=10, end=20)
    impl_b = make_node("IMPL:B", "implementation", path="src/app.py", start=30, end=40)
    store.replace_all([impl_a, impl_b], [])
    return impl_a, impl_b


# --------------------------------------------------------------------------
# framework_id_of
# --------------------------------------------------------------------------


def test_framework_id_of_pytest_convention():
    symbol = SymbolRef(
        language="python",
        kind="function",
        name="test_reuse",
        qualified_name="tests.auth.test_refresh.test_reuse",
        start_line=1,
        end_line=5,
        source="def test_reuse():\n    pass\n",
    )
    assert framework_id_of(symbol) == "tests.auth.test_refresh.test_reuse"


def test_framework_id_of_preserves_class_nesting():
    symbol = SymbolRef(
        language="python",
        kind="method",
        name="test_login",
        qualified_name="tests.api.test_auth.TestAuth.test_login",
        start_line=1,
        end_line=3,
        source="",
    )
    assert framework_id_of(symbol) == "tests.api.test_auth.TestAuth.test_login"


# --------------------------------------------------------------------------
# Suite-level execution edges
# --------------------------------------------------------------------------


def test_ingest_junit_binds_outcomes_via_test_id_map(project, store):
    junit = write_file(
        project.root,
        "reports/junit.xml",
        "<testsuite><testcase name='test_a' classname='tests.app'/></testsuite>",
    )
    result = ingest(
        project,
        store,
        junit=junit,
        revision="abc123",
        test_id_map={"tests.app.test_a": "TEST:A"},
    )
    assert result.tests_ingested == 1
    outcomes = store.outcomes_for_run(result.run_id)
    assert outcomes[0].framework_id == "tests.app.test_a"
    assert outcomes[0].test_uid is not None  # bound through the map
    assert store.latest_evidence_run("abc123") is not None


def test_ingest_unmapped_framework_id_stays_unbound(project, store):
    junit = write_file(
        project.root,
        "junit.xml",
        "<testsuite><testcase name='test_a' classname='tests.app'/></testsuite>",
    )
    result = ingest(project, store, junit=junit, revision="abc123", test_id_map={})
    assert store.outcomes_for_run(result.run_id)[0].test_uid is None


def test_ingest_suite_execution_edges_from_cobertura_intersection(project, store):
    impl_a, _impl_b = impl_graph(store)
    coverage = write_file(
        project.root,
        "coverage.xml",
        "<coverage><packages><classes>"
        "<class filename='src/app.py'><lines>"
        "<line number='12' hits='1'/><line number='15' hits='3'/>"
        "<line number='32' hits='1'/>"
        "</lines></class>"
        "</classes></packages></coverage>",
    )
    result = ingest(
        project,
        store,
        coverage=coverage,
        revision="abc123",
        impl_symbols={"src/app.py": (10, 20)},
    )
    assert result.executions_ingested == 1
    edges = store.execution_edges_for(impl_a.entity_uid)
    assert len(edges) == 1
    assert edges[0].coverage_kind == "suite"
    assert edges[0].test_uid == "suite"  # sentinel: aggregate, not per-test
    assert edges[0].hit_count == 2  # lines 12 and 15 fall inside the range


def test_ingest_no_edge_when_coverage_misses_implementation_range(project, store):
    _impl_a, _impl_b = impl_graph(store)
    coverage = write_file(
        project.root,
        "coverage.xml",
        "<coverage><packages><classes>"
        "<class filename='src/app.py'><lines><line number='1' hits='1'/>"
        "</lines></class></classes></packages></coverage>",
    )
    result = ingest(
        project,
        store,
        coverage=coverage,
        revision="abc123",
        impl_symbols={"src/app.py": (10, 20)},
    )
    assert result.executions_ingested == 0


def test_ingest_resolves_real_node_uid_over_synthetic(project, store):
    impl_a, _impl_b = impl_graph(store)
    coverage = write_file(
        project.root,
        "coverage.xml",
        "<coverage><packages><classes>"
        "<class filename='src/app.py'><lines><line number='12' hits='1'/>"
        "</lines></class></classes></packages></coverage>",
    )
    ingest(
        project,
        store,
        coverage=coverage,
        revision="abc123",
        impl_symbols={"src/app.py": (10, 20)},
    )
    # the edge binds to the indexed node's uid, not the synthetic scheme
    assert store.execution_edges_for(impl_a.entity_uid)
    assert not store.execution_edges_for("n_" + "0" * 32)


def test_ingest_per_test_edges_from_normalized_file(project, store):
    norm = write_file(
        project.root,
        "evidence.json",
        json.dumps(
            {
                "schema": "tracelayer-evidence/v1",
                "run_id": "run-norm",
                "revision": "abc123",
                "status": "pass",
                "tests": [
                    {
                        "framework_id": "tests.app.test_a",
                        "outcome": "pass",
                        "trace_id": "TEST:A",
                    }
                ],
                "execution_edges": [
                    {
                        "test_uid": "n_" + "e" * 32,
                        "implementation_uid": "n_" + "f" * 32,
                        "coverage_kind": "per_test",
                        "hit_count": 4,
                        "metadata": {"behavioral": True},
                    }
                ],
            }
        ),
    )
    result = ingest(project, store, normalized=norm, test_id_map={})
    assert result.tests_ingested == 1
    assert result.executions_ingested == 1
    edges = store.execution_edges_for("n_" + "f" * 32)
    assert edges[0].coverage_kind == "per_test"
    assert edges[0].metadata["behavioral"] is True  # level-3 metadata survives


def test_ingest_parser_failure_emits_tl051(project, store):
    junit = write_file(project.root, "junit.xml", "<testsuite><broken")
    result = ingest(project, store, junit=junit, revision="abc123")
    assert any(d.rule_id == "TL051" for d in result.diagnostics)
    assert result.tests_ingested == 0


def test_ingest_requires_at_least_one_source(project, store):
    with pytest.raises(ValueError):
        ingest(project, store)


def test_ingest_combined_status_fail_when_any_test_fails(project, store):
    junit = write_file(
        project.root,
        "junit.xml",
        "<testsuite>"
        "<testcase name='ok' classname='a'/>"
        "<testcase name='ko' classname='a'><failure/></testcase>"
        "</testsuite>",
    )
    ingest(project, store, junit=junit, revision="abc123")
    assert store.latest_evidence_run("abc123")["status"] == "fail"


def test_ingest_run_metadata_records_sources_and_require_revision(project, store):
    import json as _json

    junit = write_file(
        project.root,
        "junit.xml",
        "<testsuite><testcase name='ok' classname='a'/></testsuite>",
    )
    ingest(project, store, junit=junit, revision="abc123")
    run = store.latest_evidence_run("abc123")
    meta = _json.loads(run["metadata_json"])
    assert any("junit.xml" in s for s in meta["sources"])
    assert meta["require_revision"] is True
