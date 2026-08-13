"""Tests for tracelayer.query.context (build_context / render_context_text)."""

from __future__ import annotations

import re

from tests.conftest import make_git_repo
from tracelayer.evidence.models import ExecutionRecord
from tracelayer.evidence.models import TestOutcome as EvidenceOutcome
from tracelayer.git.repo import GitRepo
from tracelayer.query.context import build_context, render_context_text

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _ctx(store, gitrepo, trace_id):
    """build_context with a non-None assertion for test ergonomics."""
    result = build_context(store, gitrepo, trace_id)
    assert result is not None
    return result


def test_build_context_unknown_id_returns_none(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [make_node("REQ-1", "requirement")],
        [],
    )
    assert build_context(graph_store, None, "REQ-MISSING") is None


def test_build_context_upstream_ordered_by_predicate_rank(graph_store, make_node, make_edge):
    nodes = [
        make_node("WORK-1", "work"),
        make_node("REQ-1", "requirement"),
        make_node("REQ-2", "requirement"),
        make_node("ADR-1", "decision"),
        make_node("impl.one", "implementation"),
    ]
    edges = [
        make_edge("impl.one", "work", "WORK-1"),
        make_edge("impl.one", "satisfies", "REQ-1"),
        make_edge("impl.one", "addresses", "REQ-2"),
        make_edge("impl.one", "derived_from", "ADR-1"),
    ]
    graph_store.replace_all(nodes, edges)
    ctx = _ctx(graph_store, None, "impl.one")
    assert [(e.predicate, n.trace_id) for e, n in ctx.upstream] == [
        ("work", "WORK-1"),
        ("satisfies", "REQ-1"),
        ("addresses", "REQ-2"),
        ("derived_from", "ADR-1"),
    ]


def test_build_context_upstream_ties_sorted_by_trace_id(graph_store, make_node, make_edge):
    nodes = [
        make_node("WORK-1", "work"),
        make_node("WORK-2", "work"),
        make_node("impl.one", "implementation"),
    ]
    edges = [
        make_edge("impl.one", "work", "WORK-2"),
        make_edge("impl.one", "work", "WORK-1"),
    ]
    graph_store.replace_all(nodes, edges)
    ctx = _ctx(graph_store, None, "impl.one")
    assert [(e.predicate, n.trace_id) for e, n in ctx.upstream] == [
        ("work", "WORK-1"),
        ("work", "WORK-2"),
    ]


def test_build_context_downstream_ordered(graph_store, make_node, make_edge):
    nodes = [
        make_node("REQ-1", "requirement"),
        make_node("test.one", "test"),
        make_node("doc.one", "document"),
        make_node("ops.deploy", "operation"),
    ]
    edges = [
        make_edge("test.one", "verifies", "REQ-1"),
        make_edge("doc.one", "documents", "REQ-1"),
        make_edge("ops.deploy", "deploys", "REQ-1"),
    ]
    graph_store.replace_all(nodes, edges)
    ctx = _ctx(graph_store, None, "REQ-1")
    assert [(e.predicate, n.trace_id) for e, n in ctx.downstream] == [
        ("verifies", "test.one"),
        ("documents", "doc.one"),
        ("deploys", "ops.deploy"),
    ]


def test_build_context_verification_outcome_and_current(graph_store, make_node, make_edge):
    nodes = [
        make_node("impl.one", "implementation"),
        make_node("test.one", "test", meta={"framework_test_id": "tests::one"}),
        make_node("test.two", "test"),
        make_node("test.three", "test"),
    ]
    edges = [
        make_edge("test.one", "exercises", "impl.one"),
        make_edge("test.two", "exercises", "impl.one"),
        make_edge("test.three", "exercises", "impl.one"),
    ]
    graph_store.replace_all(nodes, edges)
    graph_store.add_evidence_run(
        "run-1",
        "rev1",
        "pytest",
        "ci",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:01:00Z",
        "pass",
        None,
        {"require_revision": False},
    )
    graph_store.add_test_results(
        "run-1",
        [
            EvidenceOutcome(framework_id="tests::one", outcome="pass", test_uid="n_one"),
            EvidenceOutcome(framework_id="test.two", outcome="fail", test_uid="n_two"),
        ],
    )
    ctx = _ctx(graph_store, None, "impl.one")
    assert [(v.test_trace_id, v.outcome, v.proof_level, v.current) for v in ctx.verification] == [
        ("test.one", "pass", 0, True),
        ("test.three", None, 0, False),
        ("test.two", "fail", 0, False),
    ]


def test_build_context_verification_proof_levels(graph_store, make_node, make_edge):
    nodes = [
        *[make_node(f"impl.{c}", "implementation") for c in "abcd"],
        *[make_node(f"test.{c}", "test") for c in "abcd"],
    ]
    edges = [make_edge(f"test.{c}", "exercises", f"impl.{c}") for c in "abcd"]
    graph_store.replace_all(nodes, edges)
    uids = {c: graph_store.get_node_uid(f"impl.{c}") for c in "abcd"}
    graph_store.add_evidence_run(
        "run-1",
        "rev1",
        "pytest",
        "ci",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:01:00Z",
        "pass",
        None,
        {"require_revision": False},
    )
    graph_store.add_test_results(
        "run-1",
        [
            EvidenceOutcome(framework_id=f"test.{c}", outcome="pass", test_uid=f"n_{c}")
            for c in "abcd"
        ],
    )
    graph_store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1",
                test_uid="suite",
                implementation_uid=uids["b"],
                coverage_kind="suite",
            ),
            ExecutionRecord(
                run_id="run-1",
                test_uid=graph_store.get_node_uid("test.c"),
                implementation_uid=uids["c"],
                coverage_kind="per_test",
            ),
            ExecutionRecord(
                run_id="run-1",
                test_uid=graph_store.get_node_uid("test.d"),
                implementation_uid=uids["d"],
                coverage_kind="per_test",
                metadata={"behavioral": True},
            ),
        ],
    )
    for c, expected in (("a", 0), ("b", 1), ("c", 2), ("d", 3)):
        ctx = _ctx(graph_store, None, f"impl.{c}")
        assert [
            (v.test_trace_id, v.outcome, v.proof_level, v.current) for v in ctx.verification
        ] == [
            (f"test.{c}", "pass", expected, True),
        ]


def test_build_context_staleness_status(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [
            make_node("impl.current", "implementation"),
            make_node("impl.stale", "implementation", status="stale_review_required"),
        ],
        [],
    )
    assert _ctx(graph_store, None, "impl.current").staleness == "current"
    assert _ctx(graph_store, None, "impl.stale").staleness == "stale_review_required"


def test_build_context_skips_dangling_edges(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [make_node("impl.z", "implementation")],
        [make_edge("impl.z", "satisfies", "REQ-GONE")],
    )
    ctx = _ctx(graph_store, None, "impl.z")
    assert ctx.upstream == []
    assert ctx.downstream == []


def test_build_context_provenance_with_git(tmp_path, graph_store, make_node, make_edge):
    root = make_git_repo(tmp_path, {"src/auth.py": "def login(): pass\n"})
    repo = GitRepo.open(root)
    assert repo is not None
    graph_store.replace_all(
        [make_node("impl.one", "implementation", path="src/auth.py", start=1, end=1)],
        [],
    )
    ctx = _ctx(graph_store, repo, "impl.one")
    sha = repo.rev()
    assert sha is not None
    assert _SHA.match(sha)
    assert ctx.provenance == {
        "first_seen": sha,
        "last_modified": sha,
        "commits": [sha],
    }


def test_build_context_no_provenance_without_git(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [make_node("impl.one", "implementation", path="src/auth.py", start=1, end=1)],
        [],
    )
    ctx = _ctx(graph_store, None, "impl.one")
    assert ctx.provenance == {}


def test_render_context_text_empty_layout(graph_store, make_node, make_edge):
    graph_store.replace_all(
        [make_node("REQ-X", "requirement", path="docs/req.md", start=1, end=2)],
        [],
    )
    ctx = _ctx(graph_store, None, "REQ-X")
    assert render_context_text(ctx) == (
        "REQ-X\ndocs/req.md\n\nUse:\n  trace impact REQ-X\n  trace graph REQ-X --depth 2\n"
    )


def test_render_context_text_upstream_sections_and_implements_labels(
    graph_store, make_node, make_edge
):
    nodes = [
        make_node("REQ-1", "requirement"),
        make_node("ADR-1", "decision"),
        make_node("PLAN-1", "plan"),
        make_node("impl.one", "implementation"),
    ]
    edges = [
        make_edge("impl.one", "satisfies", "REQ-1"),
        make_edge("impl.one", "implements", "ADR-1"),
        make_edge("impl.one", "implements", "PLAN-1"),
    ]
    graph_store.replace_all(nodes, edges)
    ctx = _ctx(graph_store, None, "impl.one")
    assert render_context_text(ctx) == (
        "impl.one\n"
        "\n"
        "Satisfies:\n"
        "  REQ-1 [CURRENT]\n"
        "\n"
        "Decision:\n"
        "  ADR-1\n"
        "\n"
        "Plan:\n"
        "  PLAN-1\n"
        "\n"
        "Use:\n"
        "  trace impact impl.one\n"
        "  trace graph impl.one --depth 2\n"
    )


def test_render_context_text_sections_grouped_by_header(graph_store, make_node, make_edge):
    nodes = [
        make_node("WORK-1", "work"),
        make_node("WORK-2", "work"),
        make_node("impl.one", "implementation"),
    ]
    edges = [
        make_edge("impl.one", "work", "WORK-1"),
        make_edge("impl.one", "work", "WORK-2"),
    ]
    graph_store.replace_all(nodes, edges)
    text = render_context_text(_ctx(graph_store, None, "impl.one"))
    assert "Work:\n  WORK-1\n  WORK-2\n" in text
    assert text.count("Work:") == 1


def test_render_context_text_verification_and_full_layout(graph_store, make_node, make_edge):
    nodes = [
        make_node("WORK-1", "work"),
        make_node("REQ-1", "requirement"),
        make_node("REQ-2", "requirement"),
        make_node("ADR-1", "decision"),
        make_node("impl.one", "implementation"),
        make_node("test.two", "test"),
    ]
    edges = [
        make_edge("impl.one", "work", "WORK-1"),
        make_edge("impl.one", "satisfies", "REQ-1"),
        make_edge("impl.one", "addresses", "REQ-2"),
        make_edge("impl.one", "derived_from", "ADR-1"),
        make_edge("test.two", "exercises", "impl.one"),
    ]
    graph_store.replace_all(nodes, edges)
    graph_store.add_evidence_run(
        "run-1",
        "rev1",
        "pytest",
        "ci",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:01:00Z",
        "pass",
        None,
        {"require_revision": False},
    )
    graph_store.add_test_results(
        "run-1",
        [
            EvidenceOutcome(framework_id="test.two", outcome="pass", test_uid="n_two"),
        ],
    )
    graph_store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1",
                test_uid="suite",
                implementation_uid=graph_store.get_node_uid("impl.one"),
                coverage_kind="suite",
            ),
        ],
    )
    ctx = _ctx(graph_store, None, "impl.one")
    assert render_context_text(ctx) == (
        "impl.one\n"
        "\n"
        "Work:\n"
        "  WORK-1\n"
        "\n"
        "Satisfies:\n"
        "  REQ-1 [CURRENT]\n"
        "\n"
        "Addresses:\n"
        "  REQ-2\n"
        "\n"
        "Derived from:\n"
        "  ADR-1\n"
        "\n"
        "Verification:\n"
        "  test.two  PASS  EXECUTION=L1  CURRENT\n"
        "\n"
        "Use:\n"
        "  trace impact impl.one\n"
        "  trace graph impl.one --depth 2\n"
    )


def test_render_context_text_git_section(tmp_path, graph_store, make_node, make_edge):
    root = make_git_repo(tmp_path, {"src/auth.py": "def login(): pass\n"})
    repo = GitRepo.open(root)
    graph_store.replace_all(
        [make_node("impl.one", "implementation", path="src/auth.py", start=1, end=1)],
        [],
    )
    text = render_context_text(_ctx(graph_store, repo, "impl.one"))
    lines = text.splitlines()
    assert "Git:" in lines
    idx = lines.index("Git:")
    assert _SHA.match(lines[idx + 1].removeprefix("  first_seen: "))
    assert _SHA.match(lines[idx + 2].removeprefix("  last_modified: "))
    assert lines[idx + 3] == "  commits: 1"
