"""Git repository adapter (spec FR-007, contract §V).

Wraps a git repository with deterministic, argv-array subprocess calls.
Every git failure degrades to ``None``/``[]``/``False`` — missing history is
not a trace failure (contract: "return None/[] on git errors"). All paths are
repo-root-relative; the repository root is resolved via ``rev-parse
--show-toplevel`` so subdirectory opens report the same relative paths.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_SUBPROCESS_TIMEOUT = 30


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command confined to ``root``; argv array only, no shell."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )


@dataclass
class ChangedFile:
    """One changed file relative to the repository root.

    ``change`` is one of added|modified|deleted|renamed|untracked.
    ``diff_ranges`` holds coalesced changed line ranges in the NEW file
    (1-based inclusive); ``None`` means the whole file (untracked/added
    without a worktree delta) or, for deletions, that no new-file ranges
    exist. An empty list means the file changed but no hunks were produced
    (e.g. staged-only change under plain ``git diff``).
    """

    path: str
    change: str
    old_path: str | None
    diff_ranges: list[tuple[int, int]] | None


class GitRepo:
    """Minimal git adapter used by discovery and the indexing pipeline."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def open(cls, root: Path) -> GitRepo | None:
        """Return a GitRepo for ``root``, or None when it is not a git repo.

        The repository top-level (``git rev-parse --show-toplevel``) becomes
        the working root so every reported path is root-relative.
        """
        try:
            r = _run_git(Path(root), "rev-parse", "--show-toplevel")
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return cls(Path(r.stdout.strip()))

    def root(self) -> Path:
        """Repository top-level directory."""
        return self._root

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a git command confined to the repo root (argv only, no shell)."""
        return _run_git(self._root, *args)

    def default_base(self) -> str | None:
        """Merge base with the default branch, else HEAD~1, else None.

        Used by changed-scope verification on clean checkouts (CI): the
        change set under review is HEAD vs this base.
        """
        for ref in ("origin/master", "master", "main"):
            r = self.run("merge-base", "HEAD", ref)
            if r.returncode == 0 and r.stdout.strip() and r.stdout.strip() != self.rev():
                return ref
        r = self.run("rev-parse", "HEAD~1")
        if r.returncode == 0 and r.stdout.strip():
            return "HEAD~1"
        return None

    def rev(self) -> str | None:
        """HEAD commit SHA, or None when unavailable (e.g. unborn HEAD)."""
        try:
            r = _run_git(self._root, "rev-parse", "HEAD")
        except (OSError, subprocess.SubprocessError):
            return None
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None

    def is_dirty(self) -> bool:
        """True when any tracked change or untracked file exists."""
        try:
            r = _run_git(self._root, "status", "--porcelain")
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0 and bool(r.stdout)

    def current_branch(self) -> str | None:
        """Short branch name, or None on detached HEAD / errors."""
        try:
            r = _run_git(self._root, "symbolic-ref", "--short", "HEAD")
        except (OSError, subprocess.SubprocessError):
            return None
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None

    def changed_files(self, base: str | None = None) -> list[ChangedFile]:
        """Files changed vs the working tree (default) or a base revision.

        ``base=None`` uses ``git status`` (local edit loop: what the working
        tree differs from HEAD by). With a base ref, diffs ``<base>..HEAD``
        instead (CI: the committed change set vs the merge base). Parses
        ``git status --porcelain=v1 -z --renames`` / ``git diff
        --name-status -z``; a rename entry carries the destination path
        first and the source path second (empirically verified format).
        Line ranges come from ``git diff --unified=0 -- <path>``; added
        files carry no ranges (whole-file).
        """
        try:
            if base is None:
                r = _run_git(self._root, "status", "--porcelain=v1", "-z", "--renames")
            else:
                r = _run_git(
                    self._root, "diff", "--name-status", "-z", "--find-renames", base, "HEAD"
                )
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            return []
        parts = [p for p in r.stdout.split("\x00") if p]
        changed: list[ChangedFile] = []
        i = 0
        while i < len(parts):
            if base is None:
                code, path = parts[i][:2], parts[i][3:]
            else:
                code, path = parts[i][:2], parts[i][2:]
            i += 1
            old_path: str | None = None
            if code[0] == "R" and i < len(parts):
                old_path = parts[i]
                i += 1
            x, y = code[0], code[1]
            if y == "?":
                change = "untracked"
            elif x == "R":
                change = "renamed"
            elif "D" in (x, y):
                change = "deleted"
            elif x == "A":
                change = "added"
            else:
                change = "modified"
            if change in ("added", "modified", "renamed"):
                # Lazy import avoids a repo<->diff import cycle.
                from tracelayer.git.diff import changed_line_ranges

                ranges = changed_line_ranges(self, path)
            else:
                ranges = None
            changed.append(
                ChangedFile(path=path, change=change, old_path=old_path, diff_ranges=ranges)
            )
        return changed

    def latest_modifying_commit(self, path: str) -> str | None:
        """Newest commit touching ``path`` (``git log -1``), or None."""
        try:
            r = _run_git(self._root, "log", "-1", "--format=%H", "--", path)
        except (OSError, subprocess.SubprocessError):
            return None
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None

    def first_seen_commit(self, path: str) -> str | None:
        """Oldest commit that added ``path`` (last line of ``--diff-filter=A``)."""
        try:
            r = _run_git(self._root, "log", "--diff-filter=A", "--format=%H", "--", path)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else None

    def commits_touching(self, path: str, max_count: int = 50) -> list[str]:
        """Newest-first SHAs of commits touching ``path``, up to ``max_count``."""
        try:
            r = _run_git(self._root, "log", f"--max-count={max_count}", "--format=%H", "--", path)
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            return []
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def is_ignored(self, rel_path: str) -> bool:
        """True when git ignores ``rel_path`` (``git check-ignore -q``)."""
        try:
            r = _run_git(self._root, "check-ignore", "-q", "--", rel_path)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0
