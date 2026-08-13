"""Tests for tracelayer.query.impact (semantic/structural/tests/stale)."""

from __future__ import annotations

import re

from tests.conftest import make_git_repo
from tracelayer.git.repo import GitRepo
from tracelayer.query.impact import impact

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _seed_requirement_graph(store, make_node, make_edge):
    nodes = [
        make_node("REQ-1", "requirement"),
        make_node("impl.one", "implementation"),
        make_node("impl.two", "implementation", status="stale_review_required"),
        make_node("test.one", "test"),
        make_node("test.two", "test"),
        make_node("doc.one", "document"),
    ]
    edges = [
        make_edge("impl.one", "satisfies", "REQ-1"),
        make_edge("impl.two", "satisfies", "REQ-1"),
        make_edge("test.one", "verifies", "REQ-1"),
        make_edge("doc.one", "documents", "REQ-1"),
        make_edge("test.two", "exercises", "impl.two"),
    ]
    store.replace_all(nodes, edges)


def test_impact_unknown_id_returns_empty(graph_store, make_node, make_edge):
    graph_store.replace_all([make_node("impl.one", "implementation")], [])
    r = impact(graph_store, None, "REQ-NOPE")
    assert r.semantic == []
    assert r.structural == []
    assert r.tests == []
    assert r.stale == []
    assert r.history == []


def test_impact_semantic_dependents(graph_store, make_node, make_edge):
    _seed_requirement_graph(graph_store, make_node, make_edge)
    r = impact(graph_store, None, "REQ-1")
    assert [n.trace_id for n in r.semantic] == [
        "doc.one", "impl.one", "impl.two", "test.one", "test.two",
    ]


def test_impact_tests_subset_of_semantic(graph_store, make_node, make_edge):
    _seed_requirement_graph(graph_store, make_node, make_edge)
    r = impact(graph_store, None, "REQ-1")
    assert [n.trace_id for n in r.tests] == ["test.one", "test.two"]
    assert set(n.trace_id for n in r.tests) <= set(n.trace_id for n in r.semantic)


def test_impact_stale_distinction(graph_store, make_node, make_edge):
    _seed_requirement_graph(graph_store, make_node, make_edge)
    r = impact(graph_store, None, "REQ-1")
    assert [(n.trace_id, status) for n, status in r.stale] == [
        ("impl.two", "stale_review_required"),
    ]


def test_impact_depth_cap(graph_store, make_node, make_edge):
    _seed_requirement_graph(graph_store, make_node, make_edge)
    # test.two is two hops from REQ-1 (via impl.two): excluded at depth 1.
    r = impact(graph_store, None, "REQ-1", depth=1)
    assert [n.trace_id for n in r.semantic] == [
        "doc.one", "impl.one", "impl.two", "test.one",
    ]
    r3 = impact(graph_store, None, "REQ-1", depth=3)
    assert "test.two" in [n.trace_id for n in r3.semantic]


def test_impact_semantic_only_suppresses_rest(graph_store, make_node, make_edge):
    _seed_requirement_graph(graph_store, make_node, make_edge)
    r = impact(graph_store, None, "REQ-1", semantic_only=True)
    assert [n.trace_id for n in r.semantic]
    assert r.structural == []
    assert r.tests == []
    assert r.history == []


def test_impact_structural_calls(graph_store, make_node, make_edge):
    nodes = [
        make_node("impl.three", "implementation"),
        make_node("impl.caller", "implementation"),
    ]
    edges = [make_edge("impl.caller", "calls", "impl.three", source_kind="structural")]
    graph_store.replace_all(nodes, edges)
    r = impact(graph_store, None, "impl.three", include_structural=True)
    assert [n.trace_id for n in r.structural] == ["impl.caller"]
    # Calls are structural, not semantic.
    assert r.semantic == []


def test_impact_structural_requires_flag(graph_store, make_node, make_edge):
    nodes = [
        make_node("impl.three", "implementation"),
        make_node("impl.caller", "implementation"),
    ]
    edges = [make_edge("impl.caller", "calls", "impl.three", source_kind="structural")]
    graph_store.replace_all(nodes, edges)
    r = impact(graph_store, None, "impl.three")
    assert r.structural == []


def test_impact_history_with_git(tmp_path, graph_store, make_node, make_edge):
    root = make_git_repo(tmp_path, {"src/auth.py": "def login(): pass\n"})
    repo = GitRepo.open(root)
    graph_store.replace_all(
        [make_node("impl.one", "implementation", path="src/auth.py", start=1, end=1)],
        [],
    )
    r = impact(graph_store, repo, "impl.one", include_history=True)
    assert len(r.history) == 1
    h = r.history[0]
    assert _SHA.match(h.sha)
    assert h.summary == "initial commit"


def test_impact_history_requires_flag_and_git(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [make_node("impl.one", "implementation", path="src/auth.py", start=1, end=1)],
        [],
    )
    r = impact(graph_store, None, "impl.one", include_history=True)
    assert r.history == []


def test_impact_target_node_excluded_from_own_semantic(graph_store, make_node, make_edge):
    nodes = [make_node("REQ-1", "requirement")]
    edges = [make_edge("REQ-1", "derived_from", "REQ-1")]  # self-cycle
    graph_store.replace_all(nodes, edges)
    r = impact(graph_store, None, "REQ-1")
    assert [n.trace_id for n in r.semantic] == []
