"""PostToolUse untraced-behavior nudge integration tests (spec 22.5)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace


def _hook(root, path: str) -> dict:
    r = run_trace(
        root,
        "hook",
        "post-mutation",
        "--format",
        "json",
        input=json.dumps({"path": path}),
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_new_file_with_symbols_nudges_untraced(tmp_path):
    repo = make_git_repo(tmp_path, {"src/old.py": "def old():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "new.py").write_text("def fresh():\n    return 2\n", encoding="utf-8")
    out = _hook(repo, "src/new.py")
    assert out["untraced"] == ["fresh"]
    assert "NEW UNTRACED BEHAVIOR" in out["output"]
    assert "trace:v1" in out["output"]


def test_added_symbol_to_tracked_file_nudges_only_new(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "def keep():\n    return 1\n\n\ndef added():\n    return 2\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == ["added"]


def test_edit_without_new_symbols_is_silent(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "def keep():\n    # comment-only edit\n    return 1\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == []
    assert out["output"] == ""


def test_traced_symbol_not_nudged(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "# trace:v1 id=impl.added satisfies=REQ-1\ndef added():\n    return 2\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == []
