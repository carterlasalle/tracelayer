"""Tests for the trace doctor (FR-019, contract §M).

``run_doctor`` re-detects issues (TL001 duplicates, TL002 broken refs, TL003
detached markers, TL040 unknown keys, TL110 stale, TL005 rename suggestions)
and never modifies files; ``apply_fixes`` applies only deterministic cosmetic
fixes (marker re-quoting) and never alters semantic edges.
"""

from __future__ import annotations

from pathlib import Path

from tracelayer.config import Project, TraceConfig
from tracelayer.diagnostics import SEVERITY_INFO, make
from tracelayer.doctor import apply_fixes, run_doctor
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore, entity_uid

REQUIRED = "REQUIRED"


def make_project(root: Path) -> Project:
    return Project(root=root, config=TraceConfig(repo_id="probe"))


def make_node(trace_id: str, canonical_path: str | None, **kw) -> Node:
    return Node(
        entity_uid="ignored",  # store recomputes from the deterministic scheme
        trace_id=trace_id,
        node_type="implementation",
        source_kind="declared",
        canonical_path=canonical_path,
        last_indexed_at="2026-01-01T00:00:00",
        **kw,
    )


class FakeGit:
    """Minimal gitrepo stub: old_paths returns renames for a given path."""

    def __init__(self, renames: dict[str, list[str]]) -> None:
        self._renames = renames

    def old_paths(self, canonical_path: str) -> list[str]:
        return self._renames.get(canonical_path, [])


def open_store(tmp_path: Path, nodes, edges=(), *, fts: bool = False) -> GraphStore:
    store = GraphStore.open(tmp_path / "store.sqlite3", fts=fts)
    store.replace_all(list(nodes), list(edges))
    return store


def doctor(tmp_path: Path, nodes, edges=(), *, git=None):
    project = make_project(tmp_path)
    store = open_store(tmp_path, nodes, edges)
    try:
        return run_doctor(project, store, git)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------


def test_run_doctor_emits_tl001_for_duplicate_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "dup_a.py").write_text("# trace:v1 id=impl.dup type=implementation\n")
    (src / "dup_b.py").write_text("# trace:v1 id=impl.dup type=implementation\n")
    # The store can hold only one row per trace_id, so the duplicate lives in
    # the sources; both files must be canonical paths of (distinct) nodes.
    out = doctor(
        tmp_path,
        [
            make_node("impl.aaa", "src/dup_a.py"),
            make_node("impl.bbb", "src/dup_b.py"),
        ],
    )
    tl001 = [d for d in out if d.rule_id == "TL001"]
    assert len(tl001) == 2
    assert sorted((d.path, d.line) for d in tl001) == [
        ("src/dup_a.py", 1),
        ("src/dup_b.py", 1),
    ]
    assert all(d.trace_id == "impl.dup" for d in tl001)
    assert all(d.message == "Duplicate trace ID impl.dup declared at 2 locations" for d in tl001)


def test_run_doctor_emits_tl002_for_broken_edge(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "single.py").write_text("# trace:v1 id=impl.single type=implementation\n")
    real = entity_uid("impl.single")
    bogus = "n_" + "1" * 32
    out = doctor(
        tmp_path,
        [make_node("impl.single", "src/single.py")],
        [
            Edge(
                edge_uid="ignored",
                from_uid=bogus,
                predicate="satisfies",
                to_uid=real,
                source_kind="declared",
                source_path="src/single.py",
                source_line=5,
            )
        ],
    )
    tl002 = [d for d in out if d.rule_id == "TL002"]
    assert len(tl002) == 1
    d = tl002[0]
    assert d.path == "src/single.py"
    assert d.line == 5
    assert d.message == f"Edge satisfies references missing node uid {bogus}"


def test_run_doctor_emits_rename_suggestion_via_old_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "new_name.py").write_text(
        "# trace:v1 id=impl.new_name_fn type=implementation\n"
    )
    git = FakeGit({"src/new_name.py": ["src/old_name.py"]})
    out = doctor(
        tmp_path,
        [
            make_node("impl.new_name_fn", "src/new_name.py"),
            make_node("impl.old_name_fn", "src/old_name.py"),
        ],
        git=git,
    )
    tl005 = [d for d in out if d.rule_id == "TL005"]
    assert len(tl005) == 1
    d = tl005[0]
    assert d.severity == SEVERITY_INFO
    assert d.trace_id == "impl.old_name_fn"
    assert d.path == "src/new_name.py"
    assert d.metadata["suggestion"] == "rename"
    assert d.metadata["candidate"] == "impl.new_name_fn"
    assert "Artifact renamed from src/old_name.py" in d.message


def test_run_doctor_emits_stale_tl110_suggestion(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "stale.py").write_text("# trace:v1 id=impl.stale_x type=implementation\n")
    out = doctor(
        tmp_path,
        [make_node("impl.stale_x", "src/stale.py", metadata={"status": "stale_review_required"})],
    )
    tl110 = [d for d in out if d.rule_id == "TL110"]
    assert len(tl110) == 1
    assert tl110[0].severity == SEVERITY_INFO
    assert tl110[0].trace_id == "impl.stale_x"
    assert "run `trace review impl.stale_x`" in tl110[0].message


def test_run_doctor_reemits_stored_diagnostics_and_dedups(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("# trace:v1 id=impl.x type=implementation\n")
    project = make_project(tmp_path)
    store = open_store(tmp_path, [make_node("impl.x", "src/x.py")])
    try:
        store.insert_diagnostics(
            [
                make("TL003", path="src/x.py", line=1, message="Marker for impl.x is detached"),
                make("TL003", path="src/x.py", line=1, message="Marker for impl.x is detached"),
                make("TL040", path="src/x.py", line=1, message="Unknown key 'zzz'"),
            ]
        )
        out = run_doctor(project, store, None)
    finally:
        store.close()
    tl003 = [d for d in out if d.rule_id == "TL003"]
    tl040 = [d for d in out if d.rule_id == "TL040"]
    assert len(tl003) == 1  # deterministic diagnostic UID collapses the dup
    assert len(tl040) == 1
    assert tl040[0].message == "Unknown key 'zzz'"


def test_run_doctor_output_is_sorted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dup_a.py").write_text("# trace:v1 id=impl.dup type=implementation\n")
    (tmp_path / "src" / "dup_b.py").write_text("# trace:v1 id=impl.dup type=implementation\n")
    (tmp_path / "src" / "stale.py").write_text("# trace:v1 id=impl.stale_x type=implementation\n")
    (tmp_path / "src" / "new_name.py").write_text(
        "# trace:v1 id=impl.new_name_fn type=implementation\n"
    )
    git = FakeGit({"src/new_name.py": ["src/old_name.py"]})
    out = doctor(
        tmp_path,
        [
            make_node("impl.aaa", "src/dup_a.py"),
            make_node("impl.bbb", "src/dup_b.py"),
            make_node("impl.stale_x", "src/stale.py", metadata={"status": "stale_review_required"}),
            make_node("impl.new_name_fn", "src/new_name.py"),
            make_node("impl.old_name_fn", "src/old_name.py"),
        ],
        edges=[
            Edge(
                edge_uid="ignored",
                from_uid="n_" + "1" * 32,
                predicate="satisfies",
                to_uid=entity_uid("impl.aaa"),
                source_kind="declared",
                source_path="src/dup_a.py",
                source_line=2,
            )
        ],
        git=git,
    )
    rules = [d.rule_id for d in out]
    assert rules == sorted(rules)
    assert set(rules) == {"TL001", "TL002", "TL005", "TL110"}


def test_run_doctor_does_not_modify_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    dup_a = src / "dup_a.py"
    dup_b = src / "dup_b.py"
    dup_a.write_text("# trace:v1 id=impl.dup type=implementation\n")
    dup_b.write_text("# trace:v1 id=impl.dup type=implementation\n")
    before = {p.name: p.read_text() for p in (dup_a, dup_b)}
    out = doctor(
        tmp_path,
        [
            make_node("impl.aaa", "src/dup_a.py"),
            make_node("impl.bbb", "src/dup_b.py"),
        ],
    )
    assert out
    after = {p.name: p.read_text() for p in (dup_a, dup_b)}
    assert after == before


# ---------------------------------------------------------------------------
# apply_fixes
# ---------------------------------------------------------------------------


def test_apply_fixes_requotes_value_preserving_edges(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "mod.py"
    f.write_text(
        "# trace:v1 id=impl.x type=implementation title=hello-world! work=ABC-123\ndef x(): pass\n"
    )
    project = make_project(tmp_path)
    diags = [
        make(
            "TL004",
            path="src/mod.py",
            line=1,
            message=(
                "Invalid characters in unquoted value 'hello-world!'; quote the "
                "value or use only [A-Za-z0-9._:/#@,+-]"
            ),
        )
    ]
    result = apply_fixes(project, diags)
    assert result["total_fixed"] == 1
    assert result["files"]["src/mod.py"] == {"fixed": 1, "skipped": 0}
    lines = f.read_text().splitlines()
    assert lines[0] == '# trace:v1 id=impl.x type=implementation title="hello-world!" work=ABC-123'
    assert lines[1] == "def x(): pass"


def test_apply_fixes_never_alters_semantic_edges(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "mod.py"
    f.write_text('# trace:v1 id=impl.x type=implementation title="hello-world!" work=ABC-123\n')
    project = make_project(tmp_path)
    # A quoting fix must not touch the edge: the re-quote happens on the value
    # only, and the work=ABC-123 edge survives byte-for-byte.
    diags = [make("TL004", path="src/mod.py", line=1, message="some syntax complaint")]
    apply_fixes(project, diags)
    assert f.read_text() == (
        '# trace:v1 id=impl.x type=implementation title="hello-world!" work=ABC-123\n'
    )


def test_apply_fixes_skips_non_cosmetic_diagnostics(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "mod.py"
    f.write_text("# trace:v1 id=impl.x work=ABC-123\n")
    project = make_project(tmp_path)
    # TL002 (broken edge) is a semantic issue: never fixed.
    result = apply_fixes(project, [make("TL002", path="src/mod.py", line=1, message="broken ref")])
    assert result == {"files": {}, "total_fixed": 0}
    assert f.read_text() == "# trace:v1 id=impl.x work=ABC-123\n"
    # TL004 with no fixable quoting (missing id) is skipped, not mangled.
    f2 = src / "bad.py"
    f2.write_text("# trace:v1 title=hello\n")
    result2 = apply_fixes(
        project,
        [
            make(
                "TL004",
                path="src/bad.py",
                line=1,
                message="Node-defining markers require id=<trace-id>",
            )
        ],
    )
    assert result2["total_fixed"] == 0
    assert result2["files"]["src/bad.py"]["skipped"] == 1
    assert f2.read_text() == "# trace:v1 title=hello\n"


def test_apply_fixes_skips_reorder_and_unknown_key_cases(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    project = make_project(tmp_path)
    # Field order that a canonical re-render would change: re-rendering would
    # move work= before title=, so the fix refuses (no guessing).
    reorder = src / "reorder.py"
    reorder.write_text("# trace:v1 id=impl.x type=implementation work=ABC-123 title=hello-world!\n")
    diags = [
        make("TL004", path="src/reorder.py", line=1, message="Invalid characters in unquoted value")
    ]
    result = apply_fixes(project, diags)
    assert result["files"]["src/reorder.py"]["skipped"] == 1
    assert reorder.read_text() == (
        "# trace:v1 id=impl.x type=implementation work=ABC-123 title=hello-world!\n"
    )
    # Unknown keys under permissive parsing would be lost by re-rendering:
    # also skipped.
    unknown = src / "unknown.py"
    unknown.write_text("# trace:v1 id=impl.x zzz=1 title=hello-world!\n")
    result2 = apply_fixes(
        project,
        [
            make(
                "TL004",
                path="src/unknown.py",
                line=1,
                message="Invalid characters in unquoted value",
            )
        ],
    )
    assert result2["files"]["src/unknown.py"]["skipped"] == 1
    assert unknown.read_text() == "# trace:v1 id=impl.x zzz=1 title=hello-world!\n"
