"""Commit history helpers (spec FR-007, contract §V)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from tracelayer.git.repo import GitRepo, _run_git


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: str
    summary: str


def file_history(repo: GitRepo, path: str, max_count: int = 200) -> list[CommitInfo]:
    """Newest-first commits touching ``path``, following renames.

    Runs ``git log --follow --max-count=N --format=%H%x1f%an%x1f%aI%x1f%s``.
    Parsing is tolerant: any record without all four fields is skipped.
    """
    try:
        r = _run_git(
            repo.root(),
            "log",
            "--follow",
            f"--max-count={max_count}",
            "--format=%H%x1f%an%x1f%aI%x1f%s",
            "--",
            path,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    out: list[CommitInfo] = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        out.append(
            CommitInfo(sha=parts[0], author=parts[1], date=parts[2], summary="\x1f".join(parts[3:]))
        )
    return out


def old_paths(repo: GitRepo, path: str) -> list[str]:
    """Prior path names of ``path`` from rename history (rename hints).

    Collects unique names from ``git log --follow --name-only --format=``,
    excluding ``path`` itself (the queried file appears on every commit and
    is not an "old" name). Order is first appearance, newest commit first.
    """
    try:
        r = _run_git(repo.root(), "log", "--follow", "--name-only", "--format=", "--", path)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    seen: list[str] = []
    for line in r.stdout.splitlines():
        name = line.strip()
        if not name or name == path or name in seen:
            continue
        seen.append(name)
    return seen
