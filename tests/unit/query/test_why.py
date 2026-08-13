"""Tests for tracelayer.query.why (causal why_paths)."""

from __future__ import annotations


def _chains(paths, target):
    return [[n.trace_id for _, n in p] + [target] for p in paths]


def test_why_unknown_id_returns_empty(graph_store, make_node, make_edge):
    graph_store.replace_all([make_node("impl.one", "implementation")], [])
    from tracelayer.query.why import why_paths

    assert why_paths(graph_store, "REQ-NOPE") == []


def test_why_no_predecessors_returns_empty(graph_store, make_node, make_edge):
    graph_store.replace_all([make_node("impl.one", "implementation")], [])
    from tracelayer.query.why import why_paths

    assert why_paths(graph_store, "impl.one") == []


def test_why_leaf_terminates_single_work_path(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    graph_store.replace_all(
        [make_node("WORK-X", "work"), make_node("impl.x", "implementation")],
        [make_edge("impl.x", "work", "WORK-X")],
    )
    paths = why_paths(graph_store, "impl.x")
    assert _chains(paths, "impl.x") == [["WORK-X", "impl.x"]]


def test_why_preference_order_and_ranking(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    nodes = [
        make_node("WORK-1", "work"),
        make_node("REQ-1", "requirement"),
        make_node("GOAL-1", "goal"),
        make_node("GOAL-2", "goal"),
        make_node("ADR-1", "decision"),
        make_node("PLAN-1", "plan"),
        make_node("impl.one", "implementation"),
    ]
    edges = [
        make_edge("impl.one", "work", "WORK-1"),
        make_edge("impl.one", "satisfies", "REQ-1"),
        make_edge("REQ-1", "addresses", "GOAL-1"),
        make_edge("impl.one", "implements", "PLAN-1"),
        make_edge("PLAN-1", "derived_from", "ADR-1"),
        make_edge("impl.one", "addresses", "GOAL-2"),
    ]
    graph_store.replace_all(nodes, edges)
    paths = why_paths(graph_store, "impl.one")
    assert _chains(paths, "impl.one") == [
        ["ADR-1", "PLAN-1", "impl.one"],
        ["WORK-1", "impl.one"],
        ["GOAL-1", "REQ-1", "impl.one"],
        ["GOAL-2", "impl.one"],
    ]


def test_why_cycle_terminates(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    graph_store.replace_all(
        [
            make_node("REQ-A", "requirement"),
            make_node("GOAL-A", "goal"),
            make_node("impl.a", "implementation"),
        ],
        [
            make_edge("impl.a", "satisfies", "REQ-A"),
            make_edge("REQ-A", "addresses", "GOAL-A"),
            make_edge("GOAL-A", "derived_from", "REQ-A"),
        ],
    )
    paths = why_paths(graph_store, "impl.a")
    assert _chains(paths, "impl.a") == [["GOAL-A", "REQ-A", "impl.a"]]


def test_why_max_paths_cap(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    nodes = [make_node(f"WORK-{i}", "work") for i in range(8)]
    nodes.append(make_node("impl.y", "implementation"))
    edges = [make_edge("impl.y", "work", f"WORK-{i}") for i in range(8)]
    graph_store.replace_all(nodes, edges)
    assert len(why_paths(graph_store, "impl.y", max_paths=3)) == 3
    assert len(why_paths(graph_store, "impl.y", max_paths=1)) == 1
    assert len(why_paths(graph_store, "impl.y", max_paths=0)) == 1
    assert len(why_paths(graph_store, "impl.y")) == 5  # default


def test_why_path_shape_hop_direction(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    graph_store.replace_all(
        [
            make_node("GOAL-1", "goal"),
            make_node("REQ-1", "requirement"),
            make_node("impl.one", "implementation"),
        ],
        [
            make_edge("impl.one", "satisfies", "REQ-1"),
            make_edge("REQ-1", "addresses", "GOAL-1"),
        ],
    )
    target_uid = graph_store.get_node_uid("impl.one")
    paths = why_paths(graph_store, "impl.one")
    assert len(paths) == 1
    path = paths[0]
    # Hops are root -> target; the final hop's edge leaves the target.
    assert [n.trace_id for _, n in path] == ["GOAL-1", "REQ-1"]
    assert path[-1][0].from_uid == target_uid
    assert path[-1][0].predicate == "satisfies"
    # The root has no causal predecessors.
    root_uid = path[0][0].to_uid
    assert graph_store.edges_from(root_uid) == []


def test_why_dangling_edge_skipped(graph_store, make_node, make_edge):
    from tracelayer.query.why import why_paths

    graph_store.replace_all(
        [make_node("impl.d", "implementation"), make_node("WORK-REAL", "work")],
        [
            make_edge("impl.d", "satisfies", "REQ-GONE"),
            make_edge("impl.d", "work", "WORK-REAL"),
        ],
    )
    paths = why_paths(graph_store, "impl.d")
    assert _chains(paths, "impl.d") == [["WORK-REAL", "impl.d"]]
