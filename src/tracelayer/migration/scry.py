"""Scry annotation detection (spec 33 adoption; contract §M).

v1 is detection only: ``scry:inline`` / ``scry:artifact``-style annotations are
reported for manual migration review; nothing is auto-applied.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracelayer.config import TraceConfig
from tracelayer.diagnostics import SEVERITY_INFO, Diagnostic, make
from tracelayer.migration.codeops import _iter_text_files

_SCRY_TOKEN_RE = re.compile(r"scry:([a-zA-Z][a-zA-Z0-9_-]*)")


def scan_scry(root: Path, config: TraceConfig) -> tuple[list[dict], list[Diagnostic]]:
    """Detect ``scry:inline`` / ``scry:artifact``-style annotations.

    Returns records sorted by (path, line):
    [{"path": str, "line": int, "raw": str, "kind": str}] plus one INFO
    diagnostic (TL040, nearest registered rule) per annotation noting that
    manual migration review is required. v1 performs no auto-apply.
    """
    found: list[dict] = []
    diags: list[Diagnostic] = []
    for path in _iter_text_files(root, config):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:8192]:
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), start=1):
            m = _SCRY_TOKEN_RE.search(line)
            if m is None:
                continue
            kind = m.group(1)
            found.append({"path": rel, "line": i, "raw": line.strip(), "kind": kind})
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=rel, line=i,
                message=(
                    f"scry:{kind} annotation detected; manual migration "
                    "review required (v1 detection only)"
                ),
            ))
    return found, diags
