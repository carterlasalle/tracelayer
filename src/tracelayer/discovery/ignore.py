"""Ignore logic: config excludes, gitignore, and always-ignored paths."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from tracelayer.config import TraceConfig


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob to an anchored regex.

    ``**`` matches across directories (``**/`` matches zero or more leading
    directories), ``*`` and ``?`` never cross ``/``. stdlib fnmatch treats
    ``*`` as matching ``/`` and PurePath.match fails to match root-level files
    against ``**/*``, so this small translator is the single matcher used for
    include/exclude globs.
    """
    parts: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                else:
                    parts.append(".*")
                    i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1 or j == i + 1:
                parts.append(re.escape(c))
                i += 1
            else:
                inner = pattern[i + 1 : j]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append("[" + inner + "]")
                i = j + 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def glob_match(rel_path: str, patterns: list[str]) -> bool:
    """True when the POSIX ``rel_path`` matches any glob in ``patterns``."""
    p = rel_path.replace("\\", "/")
    return any(_glob_regex(pat).match(p) for pat in patterns)


def build_ignored(
    root: Path, config: TraceConfig, git_repo: object | None = None
) -> Callable[[str], bool]:
    """Return ``is_ignored(rel_path: str) -> bool`` for discovery filtering.

    A path is ignored when it matches a ``config.discovery.exclude`` glob,
    when it lives under ``.git/**`` or ``<cache_dir>/**`` (always), or when
    git check-ignore reports it (only when ``git_repo`` is provided and
    ``config.index.respect_gitignore``). ``rel_path`` uses POSIX separators
    relative to ``root``; git errors degrade to "not ignored".
    """
    always = [".git/**", f"{config.cache_dir.rstrip('/')}/**"]
    excludes = list(config.discovery.exclude)
    use_git = git_repo is not None and config.index.respect_gitignore

    def is_ignored(rel_path: str) -> bool:
        if glob_match(rel_path, always) or glob_match(rel_path, excludes):
            return True
        if use_git:
            try:
                return bool(git_repo.is_ignored(rel_path))  # type: ignore[attr-defined]
            except Exception:
                return False
        return False

    return is_ignored
