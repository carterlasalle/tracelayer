"""DoD gap regression tests (batch: TestsDoDGaps).

Four end-to-end tests through the real ``trace`` CLI against freshly built
git repos (shared ``make_git_repo`` / ``run_trace`` helpers):

1. ``index --changed`` and ``index --all --clean`` materialize identical
   graph JSON for the same working tree, including staleness statuses.
2. A modified ``.trace/policy.toml`` is a non-blocking TL063 WARNING under
   ``verify --changed`` (exit 0).
3. ``trace graph --format jsonl`` renders deterministic node/edge lines and
   exposes the derived ``contains`` structural edge.
4. The derived ``contains`` edge is ``source_kind=structural`` with extractor
   ``tracelayer-symbols`` (extractor lives on the store edge; the CLI json
   edge carries ``source_kind``).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import make_git_repo, run_trace
from tests.integration._fixtures import chain_files, shapes_files


def _expect_ok(proc) -> None:
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def _graph_json(root: Path, trace_id: str) -> dict:
    proc = run_trace(root, "graph", trace_id, "--format", "json")
    _expect_ok(proc)
    return json.loads(proc.stdout)


def _node_status(graph: dict, trace_id: str) -> str:
    for node in graph["nodes"].values():
        if node["trace_id"] == trace_id:
            return node["status"]
    raise AssertionError(f"trace id {trace_id!r} not present in graph nodes")


def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. index --changed vs index --all --clean equivalence
# ---------------------------------------------------------------------------


def test_clean_and_incremental_index_are_equivalent(tmp_path):
    """A changed implementation plus a new marker file, then a changed
    requirement: ``index --changed`` and ``index --all --clean`` produce
    byte-identical graph JSON (nodes incl. status + edges) for the same tree,
    and both paths mark downstream nodes ``stale_review_required``."""
    root = make_git_repo(tmp_path, chain_files())
    _expect_ok(run_trace(root, "index", "--all"))

    # Edit the implementation body and add a brand-new marker file.
    impl = root / "src" / "chain.py"
    text = impl.read_text(encoding="utf-8")
    assert 'return "ok"' in text
    impl.write_text(text.replace('return "ok"', 'return "ok!"'), encoding="utf-8")
    _write(
        root,
        "docs/notes.md",
        "# Notes\n"
        "\n"
        "<!-- trace:v1 id=DOC-CHAIN-001 type=document documents=impl.chain.run -->\n"
        "\n"
        "Implementation notes for the chain feature.\n",
    )

    _expect_ok(run_trace(root, "index", "--changed"))
    incremental = _graph_json(root, "impl.chain.run")
    _expect_ok(run_trace(root, "index", "--all", "--clean"))
    clean = _graph_json(root, "impl.chain.run")

    # Same working tree, same graph — no normalization.
    assert incremental == clean

    # Both captures reflect the impl edit and the new marker file, with the
    # impl's downstream test already stale from the impl change.
    for graph in (incremental, clean):
        tids = {node["trace_id"] for node in graph["nodes"].values()}
        assert {"impl.chain.run", "REQ-CHAIN-001", "test.chain.run", "DOC-CHAIN-001"} <= tids
        assert _node_status(graph, "test.chain.run") == "stale_review_required"

    # Requirement edit: both paths mark downstream stale identically.
    req = root / "docs" / "req.md"
    req_text = req.read_text(encoding="utf-8")
    assert "must be traced" in req_text
    req.write_text(
        req_text.replace("must be traced", "must be traced and documented"), encoding="utf-8"
    )

    _expect_ok(run_trace(root, "index", "--changed"))
    stale_incremental = _graph_json(root, "impl.chain.run")
    _expect_ok(run_trace(root, "index", "--all", "--clean"))
    stale_clean = _graph_json(root, "impl.chain.run")

    assert stale_incremental == stale_clean
    for graph in (stale_incremental, stale_clean):
        assert _node_status(graph, "impl.chain.run") == "stale_review_required"
        assert _node_status(graph, "test.chain.run") == "stale_review_required"


# ---------------------------------------------------------------------------
# 2. verify --changed flags enforcement config changes (TL063, non-blocking)
# ---------------------------------------------------------------------------


def test_verify_flags_enforcement_file_changes(tmp_path):
    """A modified .trace/policy.toml is a TL063 WARNING with the policy path;
    WARNING is non-blocking so verify exits 0."""
    root = make_git_repo(tmp_path, chain_files())
    _expect_ok(run_trace(root, "index", "--all"))

    policy = root / ".trace" / "policy.toml"
    policy.write_text(
        policy.read_text(encoding="utf-8") + "\n# enforcement scope unchanged; reviewed by owner\n",
        encoding="utf-8",
    )

    proc = run_trace(root, "verify", "--changed", "--json")
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "pass"
    assert all(d["severity"] != "ERROR" for d in payload["diagnostics"])

    tl063 = [d for d in payload["diagnostics"] if d["rule"] == "TL063"]
    assert len(tl063) == 1
    assert tl063[0]["severity"] == "WARNING"
    assert tl063[0]["path"] == ".trace/policy.toml"


# ---------------------------------------------------------------------------
# 3. trace graph --format jsonl
# ---------------------------------------------------------------------------


def test_graph_jsonl_format(tmp_path):
    """trace graph --format jsonl: one JSON object per line, node lines carry
    the two trace ids, a ``contains`` edge links the class uid to the method
    uid, and ordering is deterministic (nodes by trace_id, edges by
    (from, predicate, to))."""
    root = make_git_repo(tmp_path, shapes_files())
    _expect_ok(run_trace(root, "index", "--all"))

    proc = run_trace(root, "graph", "impl.shapes.rectangle", "--format", "jsonl")
    _expect_ok(proc)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, "jsonl output must not be empty"

    objects = [json.loads(ln) for ln in lines]
    node_lines = [o for o in objects if "trace_id" in o]
    edge_lines = [o for o in objects if "from" in o]
    assert node_lines and edge_lines

    uid_by_trace = {o["trace_id"]: o["uid"] for o in node_lines}
    assert {"impl.shapes.rectangle", "impl.shapes.rectangle.area"} <= set(uid_by_trace)

    contains = [
        e
        for e in edge_lines
        if e["predicate"] == "contains"
        and e["from"] == uid_by_trace["impl.shapes.rectangle"]
        and e["to"] == uid_by_trace["impl.shapes.rectangle.area"]
    ]
    assert contains, "expected a contains edge from the class node to the method node"

    # Deterministic ordering: nodes sorted by trace_id; edges by (from, predicate, to).
    node_trace_ids = [o["trace_id"] for o in node_lines]
    assert node_trace_ids == sorted(node_trace_ids)
    edge_keys = [(e["from"], e["predicate"], e["to"]) for e in edge_lines]
    assert edge_keys == sorted(edge_keys)


# ---------------------------------------------------------------------------
# 4. derived contains edge provenance (structural, tracelayer-symbols)
# ---------------------------------------------------------------------------


def test_contains_structural_edges_derived(tmp_path):
    """The derived contains edge is exposed by trace graph --format json with
    source_kind structural; its extractor (tracelayer-symbols) lives on the
    store edge."""
    root = make_git_repo(tmp_path, shapes_files())
    _expect_ok(run_trace(root, "index", "--all"))

    graph = _graph_json(root, "impl.shapes.rectangle")
    uid_by_trace = {node["trace_id"]: uid for uid, node in graph["nodes"].items()}
    class_uid = uid_by_trace["impl.shapes.rectangle"]
    method_uid = uid_by_trace["impl.shapes.rectangle.area"]

    matches = [
        e
        for e in graph["edges"]
        if e["predicate"] == "contains" and e["from"] == class_uid and e["to"] == method_uid
    ]
    assert matches, "expected a contains edge from the class node to the method node"
    assert matches[0]["source_kind"] == "structural"

    from tracelayer.config import load_project
    from tracelayer.graph.store import GraphStore

    project, _ = load_project(root)
    store = GraphStore.open(project.db_path)
    try:
        store_edges = [
            e
            for e in store.all_edges()
            if e.from_uid == class_uid and e.predicate == "contains" and e.to_uid == method_uid
        ]
    finally:
        store.close()
    assert store_edges
    assert store_edges[0].source_kind == "structural"
    assert store_edges[0].extractor == "tracelayer-symbols"
