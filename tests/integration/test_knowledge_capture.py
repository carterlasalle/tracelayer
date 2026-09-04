"""knowledge-capture end-to-end: authoring UX from CLI to indexed query."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_git_repo, run_trace


def _repo_with_impl(tmp_path: Path) -> Path:
    repo = make_git_repo(
        tmp_path,
        {
            "src/impl.py": ("# \x74race:v1 id=impl.foo work=WORK-X\ndef foo():\n    return 1\n"),
        },
    )
    (repo / ".trace").mkdir(parents=True)
    (repo / ".trace" / "work.toml").write_text(
        '[work."WORK-X"]\ntitle = "Probe work"\n',
        encoding="utf-8",
    )
    assert run_trace(repo, "index", "--all").returncode == 0
    return repo


# trace:v1 id=test.knowledge.capture-roundtrip type=test verifies=REQ-transitive-knowledge-relevance
def test_knowledge_capture_indexes_and_surfaces(tmp_path):
    root = _repo_with_impl(tmp_path)
    proc = run_trace(
        root,
        "knowledge-capture",
        "--type",
        "anti_pattern",
        "--title",
        "No direct glob",
        "--body",
        "## Why\n\nGlob semantics diverge.",
        "--applies-to",
        "impl.foo",
        "--work",
        "WORK-X",
    )
    assert proc.returncode == 0, proc.stderr
    tid = proc.stdout.strip().splitlines()[0]
    assert tid.startswith("ANTI-")
    assert (root / "docs" / "knowledge.md").exists()
    queried = run_trace(root, "knowledge", "--for", "impl.foo")
    assert queried.returncode == 0, queried.stderr
    assert tid in queried.stdout
    assert run_trace(root, "verify", "--changed").returncode == 0
