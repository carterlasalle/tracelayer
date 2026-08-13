"""PostToolUse Write/Edit hook (spec 22.5, FR-026): dirty verification tracking.

After a mutation the hook determines which traced nodes at the path changed
(marker removed, or the containing symbol's source hash differing from the
node's stored fingerprint) and marks the linked verification dirty in session
state so the stop gate can enforce it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from tracelayer.graph.models import Node
from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    edge_target_ids,
    fit,
    linked_test_ids,
    render_allowed,
    resolve_path,
)
from tracelayer.protocol import MarkerHit, iter_marker_hits, parse_marker_hit

if TYPE_CHECKING:
    from tracelayer.graph.store import GraphStore

_MAX_BYTES = 2 * 1024 * 1024  # Threat T9 safeguard, mirrors discovery limits.
_EXT_LANG = {
    "py": "python",
    "ts": "typescript",
    "js": "javascript",
    "go": "go",
    "rs": "rust",
    "java": "java",
}


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """Detect changed traced nodes for a path and mark verification dirty.

    Also nudges the agent when the edit introduced symbols without trace
    markers (new behavior must be traced; spec 22.5 next-step guidance).
    """
    path = str(payload.get("path", ""))
    json_data = {
        "event": "post_mutation",
        "decision": "allow",
        "path": path,
        "changed": [],
        "dirty": [],
        "untraced": [],
        "output": "",
    }
    if ctx.store is None or ctx.state is None:
        return render_allowed("", json_data)
    text = _read_text(ctx.project.root, path)
    if text is None:
        return render_allowed("", json_data)
    changed = _changed_nodes(ctx, ctx.store, path, text)
    untraced = _untraced_added_symbols(ctx.project.root, path, text)
    dirty: set[str] = set()
    linked: dict[str, list[str]] = {}
    for node in changed:
        reqs = edge_target_ids(ctx.store, node.entity_uid, ("satisfies", "work"))
        tests = linked_test_ids(ctx.store, node, reqs)
        dirty.add(node.trace_id)
        dirty.update(tests)
        linked[node.trace_id] = tests
    if dirty:
        ctx.state.mark_dirty(ctx.session_id, dirty)
    text_out = _guidance(ctx, changed, linked, untraced) if (changed or untraced) else ""
    json_data.update(
        {
            "changed": [n.trace_id for n in changed],
            "dirty": sorted(dirty),
            "untraced": untraced,
            "output": text_out,
        }
    )
    return render_allowed(text_out, json_data)


def _read_text(root, rel: str) -> str | None:
    path = resolve_path(root, rel)
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parser_for(path: str):
    """Lazy symbol parser for the file's language; None when unsupported."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    language = _EXT_LANG.get(suffix)
    if language is None:
        return None
    try:
        from tracelayer.symbols.registry import get_parser

        return get_parser(language)
    except (ImportError, ValueError):
        return None


def _containing_symbol(symbols: list, line: int):
    """Narrowest symbol whose range contains the marker line, else None."""
    hits = [s for s in symbols if s.start_line <= line <= s.end_line]
    if not hits:
        return None
    return min(hits, key=lambda s: (s.end_line - s.start_line, s.start_line, s.name))


def _changed_nodes(ctx: HookContext, store: GraphStore, path: str, text: str) -> list[Node]:
    """Trace nodes at `path` whose marker or source hash indicates a change.

    Deterministic rule: a marker removed from the file always counts as
    changed; when the marker remains, the containing symbol's semantic
    fingerprint is compared against the node's stored artifact fingerprint
    (no stored fingerprint -> marker-presence only, i.e. not changed).
    """
    present: dict[str, MarkerHit] = {}
    for hit in iter_marker_hits(text, path):
        res = parse_marker_hit(hit, unknown_keys=ctx.project.config.markers.unknown_keys)
        if res.marker is not None and res.marker.trace_id:
            present[res.marker.trace_id] = hit
    parser = _parser_for(path)
    symbols = parser.parse(text, path) if parser else []
    from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint

    changed: list[Node] = []
    nodes = sorted(
        (n for n in store.all_nodes() if n.active and n.canonical_path == path),
        key=lambda n: n.trace_id,
    )
    for node in nodes:
        hit = present.get(node.trace_id)
        if hit is None:
            changed.append(node)  # marker removed
            continue
        symbol = _containing_symbol(symbols, hit.line)
        if symbol is None or node.artifact_fingerprint is None:
            continue  # no source baseline to compare (marker presence only)
        if semantic_fingerprint(normalize_block(symbol.source)) != node.artifact_fingerprint:
            changed.append(node)
    return changed


def _added_lines(root: Path, path: str) -> set[int] | None:
    """New-file line numbers introduced by the working-tree edit.

    Returns None when the file is untracked (every symbol is new), an empty
    set when git is unavailable or nothing was added.
    """
    from tracelayer.git.repo import GitRepo

    try:
        repo = GitRepo.open(root)
        if repo is None:
            return set()
        if repo.run("ls-files", "--error-unmatch", "--", path).returncode != 0:
            return None  # untracked: the whole file is new
        if repo.run("rev-parse", "HEAD").returncode != 0:
            return set()  # unborn HEAD: no baseline to diff
        r = repo.run("diff", "--unified=0", "HEAD", "--", path)
        if r.returncode != 0:
            return set()
    except (OSError, subprocess.SubprocessError):
        return set()
    added: set[int] = set()
    new_line: int | None = None
    for line in r.stdout.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m is not None:
            new_line = int(m.group(1))
            continue
        if new_line is None:
            continue
        if line.startswith("+"):
            added.add(new_line)
            new_line += 1
        # '-' (deleted) lines do not advance the new-file line counter.
    return added


def _untraced_added_symbols(root: Path, path: str, text: str) -> list[str]:
    """Symbols introduced by this edit without an attached trace marker.

    Deterministic and git-based: only symbols whose definition line is new
    in the working tree (or all symbols of an untracked file) count, so
    trivial edits to already-unmarked files stay silent.
    """
    parser = _parser_for(path)
    if parser is None:
        return []
    try:
        symbols = parser.parse(text, path)
    except Exception:
        return []
    if not symbols:
        return []
    marker_lines = {h.line for h in iter_marker_hits(text, path)}
    added = _added_lines(root, path)
    if added is None:
        selected = symbols  # untracked file: all symbols are new
    else:
        selected = [s for s in symbols if s.start_line in added]
    if not selected:
        return []
    return [
        s.name
        for s in selected
        if not any(s.start_line - 1 <= m <= s.end_line for m in marker_lines)
    ][:3]


def _guidance(
    ctx: HookContext, changed: list[Node], linked: dict[str, list[str]], untraced: list[str]
) -> str:
    """Render the spec 22.5 next-step guidance."""
    blocks: list[str] = []
    if changed:
        lines = ["TRACE CHANGE DETECTED", ""]
        all_tests: list[str] = []
        for node in changed:
            reqs = edge_target_ids(ctx.store, node.entity_uid, ("satisfies", "work"))
            lines.append(f"Changed: {node.trace_id}")
            if reqs:
                lines.append(f"Requirement: {', '.join(reqs[:3])}")
            lines.append("Semantic hash changed: yes")
            lines.append("")
            all_tests.extend(linked.get(node.trace_id, []))
        all_tests = sorted(set(all_tests))
        if all_tests:
            lines.append("Required verification now dirty:")
            lines += [f"- {t}" for t in all_tests[:8]]
            lines.append("")
        lines.append("Run linked verification, then `trace verify --changed`.")
        blocks.append("\n".join(lines))
    if untraced:
        lines = ["NEW UNTRACED BEHAVIOR", ""]
        for name in untraced:
            lines.append(f"Add a trace marker above `{name}` (e.g. for python):")
            lines.append("  # trace:v1 id=impl.<slug> work=<WORK-ID> satisfies=<REQ-ID>")
        lines.append("See the traceability skill for valid fields and formats.")
        blocks.append("\n".join(lines))
    return fit("\n\n".join(blocks), ctx.project.config.hooks.max_context_chars)
