"""Tests for tracelayer.query.search (FTS5 with LIKE fallback)."""

from __future__ import annotations

from tracelayer.graph.store import GraphStore
from tracelayer.query.search import search as query_search


def _seed_search_store(store, make_node):
    store.replace_all(
        [
            make_node("REQ-1", "requirement", title="User login requires MFA"),
            make_node(
                "impl.auth", "implementation", title="Login handler", symbol="src.auth.login"
            ),
            make_node("WORK-2", "work", meta={"work_label": "login flow"}),
            make_node("NFR-3", "nfr", meta={"summary": "performance requirement excerpt"}),
            make_node("doc.guide", "document", title="Deployment guide"),
        ],
        [],
    )


def test_search_returns_matching_nodes(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    results = graph_store.search("login")
    assert {n.trace_id for n in results} == {"REQ-1", "impl.auth", "WORK-2"}


def test_search_by_trace_id(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    results = graph_store.search("REQ-1")
    assert {n.trace_id for n in results} == {"REQ-1"}


def test_search_by_symbol_name(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    results = graph_store.search("auth")
    assert {n.trace_id for n in results} == {"impl.auth"}


def test_search_summary_and_work_label(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    assert "NFR-3" in {n.trace_id for n in graph_store.search("performance")}
    assert "NFR-3" in {n.trace_id for n in graph_store.search("excerpt")}
    assert "WORK-2" in {n.trace_id for n in graph_store.search("flow")}


def test_search_no_hits_returns_empty(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    assert graph_store.search("zzz-nomatch") == []


def test_search_respects_limit(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    results = graph_store.search("login", limit=1)
    assert len(results) == 1
    assert results[0].trace_id in {"REQ-1", "impl.auth", "WORK-2"}


def test_search_empty_or_blank_query(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    assert graph_store.search("") == []
    assert graph_store.search("   ") == []
    assert graph_store.search("login", limit=0) == []
    assert graph_store.search("login", limit=-1) == []


def test_search_malformed_fts_query_uses_fallback(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    # Unmatched quote is not a valid FTS5 query; the store must not crash.
    assert graph_store.search('"') == []


def test_search_like_fallback_without_fts(tmp_path, make_node, make_edge):
    store = GraphStore.open(tmp_path / "nofts.sqlite3", fts=False)
    try:
        _seed_search_store(store, make_node)
        assert {n.trace_id for n in store.search("REQ-1")} == {"REQ-1"}
        assert {n.trace_id for n in store.search("login")} == {
            "REQ-1",
            "impl.auth",
            "WORK-2",
        }
        assert store.search("zzz-nomatch") == []
    finally:
        store.close()


def test_query_search_wrapper_delegates(graph_store, make_node, make_edge):
    _seed_search_store(graph_store, make_node)
    assert query_search(graph_store, "REQ-1") == graph_store.search("REQ-1")
    assert query_search(graph_store, "zzz-nomatch") == []
    assert query_search(graph_store, "login", limit=2) == graph_store.search("login", limit=2)


def test_search_empty_store(graph_store, make_node, make_edge):
    graph_store.replace_all([], [])
    assert graph_store.search("anything") == []
