"""Trace doctor: re-detect, suggest, and apply cosmetic fixes (FR-019, §M).

``run_doctor`` only diagnoses and suggests — it never applies anything.
``apply_fixes`` applies deterministic cosmetic fixes ONLY (marker re-quoting),
never changing semantic edges or resolving anything by guessing (FR-019).

Rule-ID note (simplest deterministic fit, documented): rename suggestions use
TL005 (closest registered rule: IDs that no longer match the artifact) and
stale suggestions use TL110, both downgraded to INFO; migration-issue
suggestions use TL040 INFO. No new rule IDs are invented.
"""

from __future__ import annotations

import os
from pathlib import Path

from tracelayer.config import Project
from tracelayer.diagnostics import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    Diagnostic,
    make,
)
from tracelayer.graph.models import Node
from tracelayer.graph.store import GraphStore
from tracelayer.migration.codeops import build_plan, scan_codeops
from tracelayer.protocol import (
    iter_marker_hits,
    parse_marker_hit,
    parse_marker_line,
    render_marker,
)
from tracelayer.protocol.grammar import tokenize_fields

_MIGRATION_REVIEW_CLASSES = frozenset({"requires_review", "derived"})
_REQUOTE_PREFIX = "Invalid characters in unquoted value"


def _field_pairs(payload: str, path: str, line: int) -> list[tuple[str, str]]:
    """(key, value) pairs of a marker payload for faithful-fix comparison."""
    tokens, _ = tokenize_fields(payload, path=path, line=line)
    return [(t.key, t.value) for t in tokens]


def run_doctor(project: Project, store: GraphStore, gitrepo) -> list[Diagnostic]:
    """Re-detect trace issues and return suggestions (never applies anything).

    Emits, deduplicated by (rule, trace_id, path, line, message):
    - TL001 duplicate IDs (re-scanned from marker sources; the store's
      UNIQUE(trace_id) cannot hold duplicates);
    - TL002 broken edge targets (active edges referencing missing node uids);
    - TL003 detached markers (stored diagnostics plus node metadata flags);
    - TL040 unknown marker keys (re-emitted from stored diagnostics);
    - stale nodes as INFO suggestions (TL110: run ``trace review <id>``);
    - rename suggestions via git old_paths + FTS (TL005 INFO with
      metadata {"suggestion": "rename", "candidate": <id>});
    - CodeOps migration issues requiring review (TL040 INFO).

    ``gitrepo`` may be None or lack history; those paths degrade silently.
    """
    out: list[Diagnostic] = []
    seen: set[tuple] = set()

    def add(d: Diagnostic) -> None:
        key = (d.rule_id, d.trace_id, d.path, d.line, d.message)
        if key not in seen:
            seen.add(key)
            out.append(d)

    nodes = store.all_nodes(active_only=False)
    node_uids = {n.entity_uid for n in nodes}

    # TL002: active edges referencing missing node uids.
    for e in store.all_edges(status="active"):
        missing = (
            e.to_uid if e.to_uid not in node_uids
            else (e.from_uid if e.from_uid not in node_uids else None)
        )
        if missing is not None:
            add(make(
                "TL002", path=e.source_path, line=e.source_line,
                message=f"Edge {e.predicate} references missing node uid {missing}",
            ))

    # TL001: duplicate IDs across marker sources (re-scanned from disk).
    counts: dict[str, list[tuple[str, int]]] = {}
    for rel in sorted({n.canonical_path for n in nodes if n.canonical_path}):
        if Path(rel).is_absolute():
            continue
        full = project.root / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for hit in iter_marker_hits(text, rel):
            res = parse_marker_hit(hit, unknown_keys="permissive")
            tid = res.marker.trace_id if res.marker else None
            if tid:
                counts.setdefault(tid, []).append((rel, hit.line))
    for tid, locs in sorted(counts.items()):
        uniq = sorted(set(locs))
        if len(uniq) > 1:
            for rel, lineno in uniq:
                add(make(
                    "TL001", trace_id=tid, path=rel, line=lineno,
                    message=f"Duplicate trace ID {tid} declared at {len(uniq)} locations",
                ))

    # TL003: detached markers — stored diagnostics plus node metadata flags.
    for d in store.get_diagnostics(rule_id="TL003"):
        add(d)
    for n in store.all_nodes(active_only=True):
        if n.metadata.get("detached") or n.metadata.get("detached_marker"):
            add(make(
                "TL003", trace_id=n.trace_id, path=n.canonical_path,
                message=f"Marker for {n.trace_id} is detached from any supported symbol",
            ))

    # TL040: unknown marker keys (from stored diagnostics).
    for d in store.get_diagnostics(rule_id="TL040"):
        add(d)

    # Stale nodes: INFO suggestions.
    for n in store.all_nodes(active_only=True):
        if n.status() == "stale_review_required":
            add(make(
                "TL110", severity=SEVERITY_INFO, trace_id=n.trace_id,
                path=n.canonical_path,
                message=(
                    f"Stale node {n.trace_id} requires review; "
                    f"run `trace review {n.trace_id}`"
                ),
            ))

    # Rename suggestions: git old_paths + FTS (never applied).
    if gitrepo is not None and hasattr(gitrepo, "old_paths"):
        by_path: dict[str, list[Node]] = {}
        for n in nodes:
            if n.canonical_path:
                by_path.setdefault(n.canonical_path, []).append(n)
        for n in nodes:
            cp = n.canonical_path
            if not cp or Path(cp).is_absolute():
                continue
            try:
                old_names = gitrepo.old_paths(cp)
            except Exception:
                old_names = []
            for old in old_names:
                if not old or old == cp:
                    continue
                try:
                    hits = store.search(Path(old).stem, limit=10)
                except Exception:
                    hits = []
                for hit in sorted(hits, key=lambda x: (x.canonical_path or "", x.trace_id)):
                    if hit.canonical_path == old and hit.trace_id != n.trace_id:
                        add(make(
                            "TL005", severity=SEVERITY_INFO, trace_id=hit.trace_id,
                            path=cp,
                            remediation=(
                                "Rename the trace ID to the candidate or update "
                                "the marker."
                            ),
                            message=(
                                f"Artifact renamed from {old}; consider renaming "
                                f"{hit.trace_id} (candidate: {n.trace_id})"
                            ),
                            metadata={"suggestion": "rename", "candidate": n.trace_id},
                        ))
                        break

    # Migration issues: CodeOps annotations requiring review.
    markers, scan_diags = scan_codeops(project.root, project.config)
    for d in scan_diags:
        add(d)
    plan = build_plan(markers, project)
    for item in plan.items:
        for d in item.diagnostics:
            add(d)
        if item.classification in _MIGRATION_REVIEW_CLASSES:
            add(make(
                "TL040", severity=SEVERITY_INFO, path=item.path, line=item.line,
                message=(
                    f"CodeOps migration {item.classification}: {item.note}"
                ),
            ))

    out.sort(key=lambda d: (d.rule_id, d.path or "", d.line or 0,
                            d.trace_id or "", d.message))
    return out


def apply_fixes(project: Project, diagnostics: list[Diagnostic]) -> dict:
    """Apply deterministic cosmetic fixes only (marker re-quoting).

    A diagnostic is fixable iff it is TL004 with a path+line whose marker
    re-renders (``protocol.render_marker``) to a line that parses with no
    ERROR diagnostics and differs from the original. Semantic edges are never
    changed; nothing is resolved by guessing. Returns a per-file change
    report: {"files": {rel: {"fixed": int, "skipped": int}},
    "total_fixed": int}
    """
    report: dict[str, dict[str, int]] = {}
    total_fixed = 0
    grouped: dict[tuple[str, int], None] = {}
    for d in diagnostics:
        if d.rule_id != "TL004" or not d.path or d.line is None:
            continue
        grouped.setdefault((d.path, d.line), None)
    for (rel, lineno) in sorted(grouped):
        full = project.root / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.split("\n")
        counts = report.setdefault(rel, {"fixed": 0, "skipped": 0})
        if not (1 <= lineno <= len(lines)):
            counts["skipped"] += 1
            continue
        line = lines[lineno - 1]
        res = parse_marker_line(line, path=rel, line_no=lineno, unknown_keys="permissive")
        marker = res.marker
        prefix_pos = line.find("trace:v1")
        if marker is None or marker.trace_id is None or prefix_pos < 0:
            counts["skipped"] += 1
            continue
        if res.migrated:
            # Unknown keys would be lost by re-rendering; never guess.
            counts["skipped"] += 1
            continue
        # Only pure re-quoting fixes qualify: every ERROR must be an
        # unquoted-value character complaint. Aborted parses, unknown keys,
        # invalid targets, or empty values may lose content and are skipped.
        if any(
            d.severity == SEVERITY_ERROR
            and not (d.rule_id == "TL004" and d.message.startswith(_REQUOTE_PREFIX))
            for d in res.diagnostics
        ):
            counts["skipped"] += 1
            continue
        candidate = line[:prefix_pos] + render_marker(marker)
        if candidate == line:
            counts["skipped"] += 1
            continue
        # The re-render must preserve every parsed field exactly.
        if _field_pairs(line[prefix_pos:], rel, lineno) != _field_pairs(
            render_marker(marker), rel, lineno
        ):
            counts["skipped"] += 1
            continue
        reparsed = parse_marker_line(
            candidate, path=rel, line_no=lineno, unknown_keys="permissive"
        )
        if reparsed.marker is None or any(
            diag.severity == SEVERITY_ERROR for diag in reparsed.diagnostics
        ):
            counts["skipped"] += 1
            continue
        lines[lineno - 1] = candidate
        tmp = full.with_name(full.name + ".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp, full)
        counts["fixed"] += 1
        total_fixed += 1
    return {"files": report, "total_fixed": total_fixed}
