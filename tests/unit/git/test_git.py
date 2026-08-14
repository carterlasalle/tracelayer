"""Git adapter unit tests: changed-file detection, diff range coalescing,
commit history helpers, ignore checks, and subprocess safety (no shell
interpolation)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import make_git_repo
from tracelayer.git.diff import (
    changed_line_ranges,
    map_ranges_to_symbols,
    parse_unified_diff_ranges,
)
from tracelayer.git.history import file_history, old_paths
from tracelayer.git.repo import GitRepo
from tracelayer.symbols.base import SymbolRef


# trace:v1 id=test.dogfood.tests.unit.git.test_git.py type=test
def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


# --------------------------------------------------------------------- open


def test_open_non_repo_returns_none(tmp_path: Path) -> None:
    assert GitRepo.open(tmp_path) is None


def test_open_subdirectory_reports_root_relative_paths(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"sub/a.txt": "x"})
    sub_repo = GitRepo.open(root / "sub")
    assert sub_repo is not None
    assert sub_repo.root() == root.resolve()
    assert sub_repo.changed_files() == []


def test_rev_branch_and_dirty(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"a.txt": "x"})
    repo = GitRepo.open(root)
    assert repo is not None
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert repo.rev() == head
    assert repo.current_branch() == "main"
    assert repo.is_dirty() is False
    (root / "a.txt").write_text("y", encoding="utf-8")
    assert repo.is_dirty() is True


# ------------------------------------------------------------ changed files


def test_changed_files_modified_and_untracked(tmp_path: Path) -> None:
    root = make_git_repo(
        tmp_path,
        {"mod.py": "print(1)\n", "del.txt": "bye\n", "keep.txt": "k\n"},
    )
    (root / "mod.py").write_text("print(2)\n", encoding="utf-8")
    (root / "del.txt").unlink()
    (root / "new.py").write_text("print(3)\n", encoding="utf-8")
    repo = GitRepo.open(root)
    assert repo is not None
    changed = {c.path: c for c in repo.changed_files()}
    assert set(changed) == {"mod.py", "del.txt", "new.py"}
    assert changed["mod.py"].change == "modified"
    assert changed["mod.py"].diff_ranges is not None  # tracked file gets ranges
    assert changed["del.txt"].change == "deleted"
    assert changed["del.txt"].diff_ranges is None
    assert changed["new.py"].change == "untracked"
    assert changed["new.py"].diff_ranges is None  # untracked = whole file
    assert changed["mod.py"].old_path is None


def test_changed_files_added_staged(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"a.txt": "x"})
    (root / "added.txt").write_text("new\n", encoding="utf-8")
    _git(root, "add", "added.txt")
    repo = GitRepo.open(root)
    assert repo is not None
    changed = {c.path: c for c in repo.changed_files()}
    assert changed["added.txt"].change == "added"
    # Staged-only change: plain `git diff` (index vs worktree) has no hunks.
    assert changed["added.txt"].diff_ranges == []


def test_changed_files_renamed(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"old.txt": "content\n"})
    _git(root, "mv", "old.txt", "new.txt")
    repo = GitRepo.open(root)
    assert repo is not None
    changed = {c.path: c for c in repo.changed_files()}
    assert set(changed) == {"new.txt"}
    assert changed["new.txt"].change == "renamed"
    assert changed["new.txt"].old_path == "old.txt"


def test_changed_files_empty_on_clean_repo(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"a.txt": "x"})
    repo = GitRepo.open(root)
    assert repo is not None
    assert repo.changed_files() == []


# ------------------------------------------------------------- diff ranges


def test_parse_unified_diff_ranges_coalescing() -> None:
    diff = """diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -3,0 +4,2 @@
+line
+line
@@ -8,0 +11,1 @@
+line
@@ -10,3 +13,3 @@
 old
-old
+new
 old
"""
    ranges = parse_unified_diff_ranges(diff)
    # (11,11) and (13,15) have line 12 between them -> not coalesced.
    assert ranges == {"f.py": [(4, 5), (11, 11), (13, 15)]}


def test_parse_unified_diff_ranges_skips_pure_deletions_and_dev_null() -> None:
    diff = """diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-old
-old
-old
"""
    assert parse_unified_diff_ranges(diff) == {}


def test_parse_unified_diff_ranges_multiple_files() -> None:
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1,2 @@
-x
+x
+y
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,0 +6,1 @@
+new
"""
    ranges = parse_unified_diff_ranges(diff)
    assert ranges["a.py"] == [(1, 2)]
    assert ranges["b.py"] == [(6, 6)]


def test_changed_line_ranges_integration(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"f.py": "a\nb\nc\nd\ne\nf\n"})
    (root / "f.py").write_text("a\nX\nc\nd\ne\nY\n", encoding="utf-8")
    repo = GitRepo.open(root)
    assert repo is not None
    ranges = changed_line_ranges(repo, "f.py")
    assert ranges == [(2, 2), (6, 6)]  # two separate single-line edits


def test_map_ranges_to_symbols() -> None:
    syms = [
        SymbolRef("python", "function", "a", "m.a", 1, 3, "src"),
        SymbolRef("python", "function", "b", "m.b", 5, 7, "src"),
        SymbolRef("python", "function", "c", "m.c", 10, 12, "src"),
    ]
    hit = map_ranges_to_symbols(syms, [(6, 6)])
    assert [s.name for s in hit] == ["b"]
    assert map_ranges_to_symbols(syms, [(1, 2), (6, 7)]) == [syms[0], syms[1]]
    assert map_ranges_to_symbols(syms, [(20, 21)]) == []


# -------------------------------------------------------- history helpers


def test_first_seen_and_latest_modifying_commits(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"a.txt": "v1\n"})
    (root / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "second")
    repo = GitRepo.open(root)
    assert repo is not None
    first = _git(root, "log", "--diff-filter=A", "--format=%H", "--", "a.txt").stdout.splitlines()[
        -1
    ]
    latest = _git(root, "log", "-1", "--format=%H", "--", "a.txt").stdout.strip()
    assert repo.first_seen_commit("a.txt") == first
    assert repo.latest_modifying_commit("a.txt") == latest
    assert repo.first_seen_commit("a.txt") != repo.latest_modifying_commit("a.txt")
    assert repo.latest_modifying_commit("missing.txt") is None
    assert repo.first_seen_commit("missing.txt") is None


def test_commits_touching_and_history(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"a.txt": "v1\n"})
    (root / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "second")
    repo = GitRepo.open(root)
    assert repo is not None
    commits = repo.commits_touching("a.txt")
    assert len(commits) == 2
    assert repo.commits_touching("missing.txt") == []

    history = file_history(repo, "a.txt")
    assert len(history) == 2
    assert history[0].summary == "second"  # newest first
    assert history[1].summary == "initial commit"
    assert len(history[0].sha) == 40
    assert history[0].author == "TraceLayer Tests"
    assert history[0].date  # ISO date present


def test_old_paths_after_rename(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {"old.txt": "content\n"})
    _git(root, "mv", "old.txt", "new.txt")
    _git(root, "commit", "-qm", "rename")
    repo = GitRepo.open(root)
    assert repo is not None
    assert old_paths(repo, "new.txt") == ["old.txt"]
    assert old_paths(repo, "old.txt") == []


# ------------------------------------------------------------- is_ignored


def test_is_ignored(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {".gitignore": "*.log\n", "a.txt": "x"})
    repo = GitRepo.open(root)
    assert repo is not None
    assert repo.is_ignored("a.log") is True
    assert repo.is_ignored("a.txt") is False
    assert repo.is_ignored(".gitignore") is False  # tracked files aren't ignored


# ------------------------------------------------------------- safety T2/T3


def test_no_shell_interpolation_with_hostile_filename(tmp_path: Path) -> None:
    """A filename containing shell metacharacters must never be interpolated
    into a shell; argv-array invocation handles it without crashing or
    executing anything."""
    # Quotes, semicolons, $(), and backticks — but no '/' (invalid in a
    # filename) — so any shell interpolation of `touch PWNED_FILE` would
    # leave a visible side-effect file behind.
    hostile = 'evil";$(touch PWNED_FILE);"name.txt'
    root = make_git_repo(tmp_path, {"safe.txt": "x"})
    (root / hostile).write_text("data\n", encoding="utf-8")
    repo = GitRepo.open(root)
    assert repo is not None
    changed = repo.changed_files()
    paths = [c.path for c in changed]
    assert hostile in paths  # detected intact, no crash
    assert repo.is_ignored(hostile) is False  # check-ignore with weird path
    assert changed_line_ranges(repo, hostile) == []  # untracked diff path
    assert repo.first_seen_commit(hostile) is None  # never committed
    # The injected command would have created this file if any shell ran.
    assert not (root / "PWNED_FILE").exists(), "shell was interpolated!"
