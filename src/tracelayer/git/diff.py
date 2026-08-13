"""Unified-diff line range parsing (spec FR-007, contract §V)."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from tracelayer.git.repo import _run_git

if TYPE_CHECKING:
    from tracelayer.git.repo import GitRepo
    from tracelayer.symbols.base import SymbolRef

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _coalesce(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent (gap of one) ranges, sorted by start."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    out = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = out[-1]
        if start <= prev_end + 1:
            out[-1] = (prev_start, max(prev_end, end))
        else:
            out.append((start, end))
    return out


def parse_unified_diff_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse ``git diff --unified=0`` text into per-file changed line ranges.

    Returns ``{new_path: [(start, end), ...]}`` keyed by the new-side path
    with any ``a/``/``b/`` prefix stripped, ranges 1-based inclusive and
    coalesced. Files without hunks map to ``[]``; pure-deletion hunks
    (``+0,0``) are skipped. Headers naming ``/dev/null`` are ignored.
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            if name == "/dev/null":
                current = None
                continue
            current = name[2:] if name.startswith(("a/", "b/")) else name
            ranges.setdefault(current, [])
        elif current is not None and line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(3))
            count = int(m.group(4) or 1)
            if start == 0 or count == 0:  # pure deletion: no new-side lines
                continue
            ranges[current].append((start, start + count - 1))
    return {path: _coalesce(rs) for path, rs in ranges.items()}


def changed_line_ranges(repo: GitRepo, path: str) -> list[tuple[int, int]]:
    """Changed line ranges of ``path`` in the working tree (new file).

    Runs ``git diff --unified=0 -- <path>`` (index vs worktree); a staged-only
    change therefore yields ``[]``. Empty list on git errors as well.
    """
    try:
        r = _run_git(repo.root(), "diff", "--unified=0", "--", path)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    parsed = parse_unified_diff_ranges(r.stdout)
    norm = path[2:] if path.startswith(("a/", "b/")) else path
    return parsed.get(norm, [])


def map_ranges_to_symbols(
    symbols: list[SymbolRef], ranges: list[tuple[int, int]]
) -> list[SymbolRef]:
    """Symbols whose [start_line, end_line] intersects any range (input order)."""
    return [
        s
        for s in symbols
        if any(s.start_line <= end and start <= s.end_line for start, end in ranges)
    ]
