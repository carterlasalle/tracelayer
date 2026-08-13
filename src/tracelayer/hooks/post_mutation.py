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
from typing import TYPE_CHECKING, Any

from tracelayer.graph.models import Node
from tracelayer.hooks.common import (
    HookContext,
    HookOutput,
    edge_target_ids,
    fit,
    linked_test_ids,
    render_allowed,
    render_blocked,
    resolve_path,
)
from tracelayer.protocol import MarkerHit, iter_marker_hits, parse_marker_hit
from tracelayer.protocol.ontology import SEMANTIC_EDGES

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


# Node types whose content change marks downstream artifacts stale (spec 22.7).
UPSTREAM_TYPES = frozenset({"requirement", "prd", "decision", "plan"})


def handle(ctx: HookContext, payload: dict) -> HookOutput:
    """PostToolUse Write/Edit hook (spec 22.4-22.8, FR-026).

    - marks linked verification dirty when traced nodes changed (22.5);
    - teaches the agent how to trace brand-new files (22.4);
    - flags requirement/ADR/plan edits with stale downstream artifacts (22.7);
    - blocks deletions that leave unresolved trace edges (22.8);
    - nudges added symbols without markers in tracked files.
    """
    path = str(payload.get("path", ""))
    json_data = {
        "event": "post_mutation",
        "decision": "allow",
        "path": path,
        "changed": [],
        "dirty": [],
        "untraced": [],
        "new_file": False,
        "deleted": [],
        "stale_downstream": [],
        "output": "",
    }
    if ctx.store is None or ctx.state is None:
        return render_allowed("", json_data)
    text = _read_text(ctx.project.root, path)
    if text is None:
        return _deleted_path_output(
            ctx.store, path, json_data, ctx.project.config.hooks.max_context_chars
        )
    changed, deleted = _changed_nodes(ctx, ctx.store, path, text)
    untraced, is_new_file = _untraced_added_symbols(ctx.project.root, path, text)
    dirty: set[str] = set()
    linked: dict[str, list[str]] = {}
    for node in changed + deleted:
        reqs = edge_target_ids(ctx.store, node.entity_uid, ("satisfies", "work"))
        tests = linked_test_ids(ctx.store, node, reqs)
        dirty.add(node.trace_id)
        dirty.update(tests)
        linked[node.trace_id] = tests
    if dirty:
        ctx.state.mark_dirty(ctx.session_id, dirty)
    block = _deleted_block(ctx.store, deleted, ctx.project.config.hooks.max_context_chars)
    if block is not None:
        return render_blocked(
            block,
            {
                **json_data,
                "decision": "block",
                "changed": [n.trace_id for n in changed + deleted],
                "deleted": [n.trace_id for n in deleted],
                "output": block,
            },
        )
    downstream = _stale_downstream(ctx.store, changed)
    text_out = (
        _guidance(ctx, changed, deleted, linked, untraced, is_new_file, downstream)
        if (changed or deleted or untraced or downstream)
        else ""
    )
    json_data.update(
        {
            "changed": [n.trace_id for n in changed + deleted],
            "dirty": sorted(dirty),
            "untraced": untraced,
            "new_file": is_new_file,
            "deleted": [n.trace_id for n in deleted],
            "stale_downstream": downstream,
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


def _changed_nodes(
    ctx: HookContext, store: GraphStore, path: str, text: str
) -> tuple[list[Node], list[Node]]:
    """(modified, deleted) trace nodes at ``path`` after this edit.

    Deterministic rule: a marker removed from the file always counts as
    deleted; when the marker remains, the containing symbol's semantic
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
    blocks = _markdown_blocks(ctx, path, text)
    from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint

    modified: list[Node] = []
    deleted: list[Node] = []
    nodes = sorted(
        (n for n in store.all_nodes() if n.active and n.canonical_path == path),
        key=lambda n: n.trace_id,
    )
    for node in nodes:
        hit = present.get(node.trace_id)
        if hit is None:
            deleted.append(node)  # marker removed
            continue
        symbol = _containing_symbol(symbols, hit.line)
        if symbol is not None and node.artifact_fingerprint is not None:
            if semantic_fingerprint(normalize_block(symbol.source)) != node.artifact_fingerprint:
                modified.append(node)
            continue
        block = blocks.get(node.trace_id)
        if block is not None and node.artifact_fingerprint is not None:
            # Markdown fingerprints exclude the heading line; the title is
            # the semantic content for requirements/ADRs, so compare both.
            if block.fingerprint != node.artifact_fingerprint or block.title != node.title:
                modified.append(node)
            continue
        # no source baseline to compare (marker presence only)
    return modified, deleted


def _markdown_blocks(ctx: HookContext, path: str, text: str) -> dict[str, Any]:
    """trace_id -> MarkdownBlock for doc files (spec 22.7 change detection)."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix not in ("md", "mdx", "markdown"):
        return {}
    try:
        from tracelayer.artifacts.markdown import extract_markdown_blocks

        blocks = extract_markdown_blocks(path, text, ctx.project.config)
        return {b.trace_id: b for b in blocks if b.trace_id}
    except Exception:
        return {}


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


def _untraced_added_symbols(root: Path, path: str, text: str) -> tuple[list[str], bool]:
    """(symbols introduced by this edit, is_new_file).

    Deterministic and git-based: only symbols whose definition line is new
    in the working tree (or all symbols of an untracked file) count, so
    trivial edits to already-unmarked files stay silent. The new-file flag
    drives the spec 22.4 guidance (judgment) vs the 22.5 nudge (marker).
    """
    parser = _parser_for(path)
    if parser is None:
        return [], False
    try:
        symbols = parser.parse(text, path)
    except Exception:
        return [], False
    if not symbols:
        return [], False
    marker_lines = {h.line for h in iter_marker_hits(text, path)}
    added = _added_lines(root, path)
    if added is None:
        selected = symbols  # untracked file: all symbols are new
    else:
        selected = [s for s in symbols if s.start_line in added]
    if not selected:
        return [], False
    return (
        [
            s.name
            for s in selected
            if not any(s.start_line - 1 <= m <= s.end_line for m in marker_lines)
        ][:3],
        added is None,
    )


def _deleted_path_output(
    store: GraphStore, path: str, json_data: dict, max_chars: int
) -> HookOutput:
    """Spec 22.8: the file itself is gone — every traced node at it is deleted."""
    deleted = [n for n in store.all_nodes() if n.active and n.canonical_path == path]
    if not deleted:
        return render_allowed("", json_data)
    block = _deleted_block(store, deleted, max_chars)
    if block is not None:
        return render_blocked(
            block,
            {
                **json_data,
                "decision": "block",
                "deleted": [n.trace_id for n in deleted],
                "output": block,
            },
        )
    note = _deleted_note(deleted)
    return render_allowed(
        note,
        {
            **json_data,
            "deleted": [n.trace_id for n in deleted],
            "output": note,
        },
    )


def _deleted_block(store: GraphStore, deleted: list[Node], max_chars: int) -> str | None:
    """Spec 22.8: block when deletion leaves unresolved reference edges."""
    for node in deleted:
        refs: set[tuple[str, str]] = set()
        for edge in store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate not in SEMANTIC_EDGES:
                continue
            src = store.get_node(uid=edge.from_uid)
            if src is not None and src.active:
                refs.add((src.trace_id, edge.predicate))
        if not refs:
            continue
        lines = [
            "TRACE DELETION REQUIRES ACTION",
            "",
            f"`{node.trace_id}` was deleted, but these still reference it:",
        ]
        for tid, pred in sorted(refs)[:8]:
            lines.append(f"- {tid} ({pred})")
        lines += [
            "",
            "Deletion leaves unresolved trace edges. Either:",
            "1. Retire/replace the references (update their markers, or add a",
            "   `supersedes=` successor for the deleted behavior), or",
            "2. Restore the traced behavior.",
            "",
            "Then retry.",
        ]
        return fit("\n".join(lines), max_chars)
    return None


def _deleted_note(deleted: list[Node]) -> str:
    """Spec 22.8: preserve trace identity through renames/moves."""
    ids = ", ".join(n.trace_id for n in deleted[:3])
    lines = [
        "TRACE MARKER REMOVED",
        "",
        f"No remaining references to {ids}.",
        "If this was a rename or move, preserve the stable trace ID (move the",
        "marker with the behavior); provenance and structural attachment",
        "update automatically.",
    ]
    return "\n".join(lines)


def _stale_downstream(store: GraphStore, changed: list[Node]) -> dict[str, list[str]]:
    """Spec 22.7: requirement/ADR/plan edits with active downstream references."""
    out: dict[str, list[str]] = {}
    for node in changed:
        if node.node_type not in UPSTREAM_TYPES:
            continue
        downstream: set[str] = set()
        for edge in store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate not in SEMANTIC_EDGES:
                continue
            src = store.get_node(uid=edge.from_uid)
            if src is not None and src.active:
                downstream.add(src.trace_id)
        if downstream:
            out[node.trace_id] = sorted(downstream)[:8]
    return out


def _guidance(
    ctx: HookContext,
    changed: list[Node],
    deleted: list[Node],
    linked: dict[str, list[str]],
    untraced: list[str],
    is_new_file: bool,
    downstream: dict[str, list[str]],
) -> str:
    """Render the spec 22.4-22.8 next-step guidance, bounded."""
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
    if deleted:
        blocks.append(_deleted_note(deleted))
    for tid, downstream_ids in downstream.items():
        lines = [f"{tid.upper()} CHANGED", ""]
        lines.append("Downstream artifacts marked stale:")
        lines += [f"- {t}" for t in downstream_ids]
        lines.append("")
        lines.append("Prior evidence remains historical but is no longer current.")
        lines.append("Review downstream relationships before completion.")
        blocks.append("\n".join(lines))
    if untraced:
        if is_new_file:
            lines = ["NEW ARTIFACT CREATED", ""]
            lines.append("This file has no trace marker yet. Candidates:")
            lines += [f"- {name}" for name in untraced]
            lines += [
                "",
                "If this file introduces a meaningful behavior boundary (public",
                "API, business rule, security boundary, persistence/migration,",
                "verification test, ...), create or reuse a trace ID and link it",
                "semantically, e.g.:",
                "  # trace:v1 id=impl.<slug> work=<WORK-ID> satisfies=<REQ-ID>",
                "Do not trace imports, boilerplate, generated code, or trivial",
                "helpers.",
            ]
        else:
            lines = ["NEW UNTRACED BEHAVIOR", ""]
            for name in untraced:
                lines.append(f"Add a trace marker above `{name}` (e.g. for python):")
                lines.append("  # trace:v1 id=impl.<slug> work=<WORK-ID> satisfies=<REQ-ID>")
            lines.append("See the traceability skill for valid fields and formats.")
        blocks.append("\n".join(lines))
    return fit("\n\n".join(blocks), ctx.project.config.hooks.max_context_chars)
