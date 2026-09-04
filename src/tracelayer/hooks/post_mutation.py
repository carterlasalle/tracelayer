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


# trace:v1 id=impl.hooks.post-mutation-handle work=WORK-TL-001
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
    # Whole-tree reconcile FIRST: clear stale obligations before any
    # scan creates new ones — this is what prevents the "N obligations
    # pending" message from re-appearing on every OMP prompt.
    try:
        from tracelayer.hooks.post_mutation import reconcile_pending_obligations as _reconcile

        _reconcile(ctx.project, ctx.state, ctx.session_id)
    except Exception:
        pass
    if not path:
        return _scan_changed_files(ctx, json_data)
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
    _resolve_obligations(ctx, path, text)
    if ctx.state.pending_spec_update(ctx.session_id) and ctx.store is not None:
        try:
            from tracelayer.tasks import _reconcile_spec_updates

            _reconcile_spec_updates(ctx.project, ctx.store, ctx.session_id)
        except Exception:
            pass  # intake reconciliation never breaks the edit loop
    if ctx.state.active_work(ctx.session_id):
        try:
            from tracelayer.tasks import record_receipt

            targets = [n.trace_id for n in changed + deleted]
            if not targets:
                targets = [
                    n.trace_id
                    for n in ctx.store.all_nodes(active_only=True)
                    if n.canonical_path == path
                ]
            record_receipt(
                ctx.project,
                ctx.session_id,
                path=path,
                operation="modify",
                targets=targets,
                harness="hook",
            )
        except Exception:
            pass  # receipts are derived history; never break the edit loop
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
        _guidance(ctx, path, changed, deleted, linked, untraced, is_new_file, downstream)
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
    # Post-scan reconcile: clear stale obligations that the scan didn't
    # need to create. Update json_data so the output reflects the
    # reconciled state, not the pre-reconcile snapshot.
    try:
        from tracelayer.hooks.post_mutation import reconcile_pending_obligations as _reconcile

        reconciled, remaining = _reconcile(ctx.project, ctx.state, ctx.session_id)
        if reconciled or remaining is not None:
            json_data["pending_obligations"] = remaining
            json_data["reconciled_obligations"] = reconciled
    except Exception:
        pass
    return render_allowed(text_out, json_data)


# trace:exempt reason=internal-helper
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


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
def _containing_symbol(symbols: list, line: int):
    """Narrowest symbol whose range contains the marker line, else None."""
    hits = [s for s in symbols if s.start_line <= line <= s.end_line]
    if not hits:
        return None
    return min(hits, key=lambda s: (s.end_line - s.start_line, s.start_line, s.name))


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:v1 id=impl.hooks.obligation-resolution work=WORK-TL-001
def _untraced_boundaries(ctx: HookContext, path: str, text: str) -> list:
    """Boundary objects for added symbols without markers (for suggestions)."""
    from tracelayer.discovery.boundaries import boundary_is_traced, extract_boundaries

    try:
        boundaries = extract_boundaries(path, text)
    except Exception:
        return []
    return [
        b
        for b in boundaries
        if not boundary_is_traced(text, boundaries, b, ctx.project.root, ctx.store)
    ]


# trace:v1 id=impl.hooks.post-bash-scan work=WORK-TL-001
def _scan_changed_files(ctx: HookContext, json_data: dict) -> HookOutput:
    """Bash/generator mode: no path was given, so diff the working tree.

    Every changed file with untraced behavioral boundaries becomes a durable
    obligation (the same authoring loop the Write/Edit gate enforces), so
    opaque mutations cannot escape coaching.
    """
    from tracelayer.discovery.boundaries import boundary_is_traced, extract_boundaries
    from tracelayer.git.repo import GitRepo

    repo = GitRepo.open(ctx.project.root)
    if repo is None:
        return render_allowed("", json_data)
    files = repo.changed_files()
    work = ctx.state.active_work(ctx.session_id) if ctx.state else None
    reqs = ctx.state.active_requirements(ctx.session_id) if ctx.state else None
    req = reqs[0] if reqs and len(reqs) == 1 else None
    plan = ctx.state.active_plan(ctx.session_id) if ctx.state else None
    try:
        from tracelayer.hooks.common import policy_excluded
    except Exception:
        policy_excluded = None
    # Reconcile stale obligations FIRST: if markers exist in the tree,
    # resolve them before the scan re-creates the same ones. This is what
    # prevents the recurring "N obligations already pending" messages.
    try:
        from tracelayer.hooks.post_mutation import reconcile_pending_obligations as _reconcile

        _reconcile(ctx.project, ctx.state, ctx.session_id)
    except Exception:
        pass
    created: list[str] = []
    remaining: dict[str, str] = {}
    pending_seen: list[str] = []
    for f in sorted(files, key=lambda x: x.path)[:20]:
        if f.change == "deleted":
            continue
        if f.path.startswith(".trace/") or f.path.startswith(".git/") or f.path in ("AGENTS.md",):
            continue  # internal TraceLayer state is not scanned behavior
        # Skip test files and CLI by default — they're infrastructure, not product behavior
        if re.search(
            r"(?:^|/)tests?/(?:test_|.*_test\.py|.*\.test\.\w+|.*\.spec\.\w+)|(?:^|/)__(?:tests|spec)__/(?!init)|test_[a-z_]+\.py$|[a-z_]+_test\.py$",
            f.path,
        ):
            continue
        if f.path.endswith("/cli.py") or "/cli/" in f.path:
            continue
        if policy_excluded is not None:
            try:
                if policy_excluded(ctx.project, f.path, ctx.gitrepo):
                    continue  # excluded by policy or .gitignore
            except Exception:
                pass
        text = _read_text(ctx.project.root, f.path)
        if text is None:
            continue
        _resolve_obligations(ctx, f.path, text)  # Bash-authored markers resolve here too
        if ctx.state.pending_spec_update(ctx.session_id) and ctx.store is not None:
            try:
                from tracelayer.tasks import _reconcile_spec_updates

                _reconcile_spec_updates(ctx.project, ctx.store, ctx.session_id)
            except Exception:
                pass  # intake reconciliation never breaks the edit loop
        try:
            boundaries = extract_boundaries(f.path, text)
            untraced = [
                b
                for b in boundaries
                if not boundary_is_traced(text, boundaries, b, ctx.project.root, ctx.store)
            ]
        except Exception:
            untraced = []
        existing_ids = _ids_in_file(text)
        for b in untraced:
            from tracelayer.discovery.suggest import suggest_marker

            suggestion = suggest_marker(
                b,
                f.path,
                work=work,
                requirement=req,
                plan=plan,
                existing_ids=existing_ids,
            )
            added = ctx.state.add_obligation(
                ctx.session_id,
                {
                    "path": f.path,
                    "symbol": b.qualified_name or b.name,
                    "kind": "new_behavior",
                    "work": work or "",
                    "requirement": req or "",
                    "suggested_marker": suggestion.marker,
                    "state": "pending",
                },
            )
            label = f"{f.path}::{b.name}"
            if added:
                created.append(label)
            else:
                pending_seen.append(label)
            remaining[label] = str(suggestion.marker)
    if not (created or pending_seen):
        return render_allowed("", json_data)
    lines = []
    if created:
        lines.append(f"BASH MUTATION DETECTED — {len(created)} NEW TRACE OBLIGATION(S)")
        lines.append("")
        for item in created[:10]:
            lines.append(f"- {item} (new)")
        if len(created) > 10:
            lines.append(f"  ... and {len(created) - 10} more")
    if pending_seen:
        if created:
            lines.append("")
        lines.append(f"PLUS {len(pending_seen)} obligation(s) already pending (unchanged):")
        for item in sorted(set(pending_seen))[:10]:
            lines.append(f"- {item}")
        if len(set(pending_seen)) > 10:
            lines.append(f"  ... and {len(set(pending_seen)) - 10} more")
    if reqs and len(reqs) > 1:
        lines.append("")
        lines.append("Candidate requirements (choose per boundary):")
        lines += [f"- {rid}" for rid in reqs]
    lines.append("")
    lines.append("Resolve each before completing (or `trace verify --changed`).")
    text = "\n".join(lines)
    json_data["output"] = text
    json_data["created_obligations"] = created
    json_data["pending_obligations"] = sorted(set(pending_seen))
    return render_allowed(text, json_data)


# trace:v1 id=impl.hooks.resolve-obligations work=WORK-TL-001
def _resolve_obligations(ctx: HookContext, path: str, text: str) -> int:
    """Resolve the CURRENT session's pending obligations for ``path``."""
    if ctx.state is None:
        return 0
    return _resolve_obligations_in(ctx.state, ctx.session_id, ctx.project, path, text)


# trace:exempt reason=internal-helper
def _unchanged_since_base(project, path: str, boundary) -> bool:
    """True when the boundary is fingerprint-identical to the change base.

    Mirrors TL013 (same qualified-name key, same semantic fingerprint): a
    boundary the work did not touch needs no marker. Fail-closed — any
    uncertainty keeps the obligation pending.
    """
    try:
        from tracelayer.discovery.boundaries import extract_boundaries
        from tracelayer.git.repo import GitRepo
        from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint

        repo = GitRepo(project.root)
        base = repo.default_base()
        if base is None:
            return False
        proc = repo.run("show", f"{base}:{path}")
        if proc.returncode != 0:
            return False
        key = str(getattr(boundary, "qualified_name", "") or boundary.name)
        current = semantic_fingerprint(normalize_block(boundary.source))
        for old in extract_boundaries(path, proc.stdout):
            if str(getattr(old, "qualified_name", "") or old.name) == key:
                return semantic_fingerprint(normalize_block(old.source)) == current
        return False
    except Exception:
        return False


# trace:v1 id=impl.hooks.reconcile-base-fp work=WORK-reconcile-unchanged-boundary-obligations-against-the-change-base satisfies=REQ-base-fingerprint-reconciliation
def _resolve_obligations_in(state, session_id: str, project, path: str, text: str) -> int:
    """Mark pending trace obligations satisfied when the expected boundary
    is actually traced (adversarial review P0).

    Session-parameterized so the stop gate and finalizer can reconcile a
    session's stale obligation snapshot against the CURRENT tree: an
    obligation whose marker landed (in any session) is a repo fact, not a
    session fact — it must not block completion forever (system_ir
    transcript: 136 persisted obligations, verify passing, gate still
    blocking on a stale 8-item list).

    An obligation for (path, expected-symbol) resolves when the expected
    boundary has ANY marker attached — including exemption markers (the
    agent chose ``# trace:exempt reason=...`` instead of the suggested
    trace id). A marker attached to an UNRELATED boundary does NOT resolve
    the obligation; a mismatch is reported so the agent can confirm a real
    rename via ``trace task resolve-obligation <path> <symbol>``.
    """
    parser = _parser_for(path)
    try:
        symbols = parser.parse(text, path) if parser else []
    except Exception:
        symbols = []
    marker_ids: set[str] = set()
    for hit in iter_marker_hits(text, path):
        res = parse_marker_hit(hit, unknown_keys=project.config.markers.unknown_keys)
        if res.marker is not None and res.marker.trace_id:
            marker_ids.add(res.marker.trace_id)
    from tracelayer.discovery.boundaries import boundary_is_traced
    from tracelayer.discovery.suggest import _slug

    resolved = 0
    for obl in state.pending_obligations(session_id):
        if obl.get("path") != path:
            continue
        symbol_name = obl.get("symbol")
        if not isinstance(symbol_name, str) or not symbol_name:
            continue
        # the expected boundary, found by qualified name or slug (cosmetic
        # renames like saveUser -> save_user keep the slug). Exact match
        # wins; the slug fallback applies ONLY when it is unambiguous —
        # multiple same-slug boundaries would let a marker on an unrelated
        # sibling resolve the obligation (false gate pass).
        exact = [
            s for s in symbols if str(getattr(s, "qualified_name", "") or s.name) == symbol_name
        ]
        expected = exact[0] if exact else None
        if expected is None:
            want = _slug(symbol_name.rsplit(".", 1)[-1])
            by_slug = [s for s in symbols if _slug(str(s.name)) == want]
            if len(by_slug) == 1:
                expected = by_slug[0]
        if expected is None:
            # Boundary no longer exists. Stale ONLY when the file doesn't
            # carry the obligation's suggested marker id: a file rewritten
            # with the suggested id on a DIFFERENTLY-NAMED boundary is an
            # unresolved rename (adversarial FINDING 8), not a stale list.
            suggested_ids = _ids_in(str(obl.get("suggested_marker", "")))
            if suggested_ids and (suggested_ids & marker_ids):
                continue  # rename mismatch — keep blocking, agent must confirm
            state.resolve_obligation(session_id, path, symbol_name)
            resolved += 1
            continue
        # Use boundary_is_traced which handles both v1 and exempt markers
        if boundary_is_traced(text, symbols, expected, project.root, None):
            state.resolve_obligation(session_id, path, symbol_name)
            resolved += 1
            continue
        # Unchanged since the change base: the boundary predates the work, so
        # TL013 needs nothing from it and the file-granular obligation is
        # stale. Absent from (or changed vs) the base, it stays pending.
        if _unchanged_since_base(project, path, expected):
            state.resolve_obligation(session_id, path, symbol_name)
            resolved += 1
            continue
    return resolved


# trace:v1 id=impl.hooks.reconcile-obligations work=WORK-TL-001
def reconcile_pending_obligations(project, state, session_id: str) -> tuple[int, list[dict]]:
    """Reconcile a session's pending obligations against the CURRENT tree.

    Returns (resolved_count, remaining_pending). Each pending obligation's
    file is parsed and the boundary-attachment rule applied — an obligation
    whose expected boundary now carries a marker is satisfied regardless of
    which session's hook landed it. This is what lets the stop gate and
    finalizer trust the repository over a stale session snapshot.
    """
    if state is None:
        return 0, []
    pending = state.pending_obligations(session_id)
    if not pending:
        return 0, []
    by_path: dict[str, list[dict]] = {}
    for obl in pending:
        by_path.setdefault(str(obl.get("path") or ""), []).append(obl)
    total_resolved = 0
    for path, _obls in by_path.items():
        if not path:
            continue
        text = _read_text(project.root, path)
        if text is None:
            continue
        try:
            total_resolved += _resolve_obligations_in(state, session_id, project, path, text)
        except Exception:
            pass  # reconciliation never breaks the gate
    return total_resolved, state.pending_obligations(session_id)


# trace:exempt reason=internal-detail
def _attachment_gap_ok(text: str, marker_line: int, symbol_start: int) -> bool:
    """Only blank/comment/decorator lines may separate marker and symbol."""
    for i in range(marker_line + 1, symbol_start):
        stripped = text.splitlines()[i].strip() if i < len(text.splitlines()) else ""
        if stripped and not stripped.startswith(("#", "//", "@", "/*", "*")):
            return False
    return True


# trace:exempt reason=internal-detail
def _ids_in(marker_text: str) -> set[str]:
    """Trace ids referenced by a suggested marker line."""
    try:
        for hit in iter_marker_hits(marker_text, "suggested"):
            res = parse_marker_hit(hit, unknown_keys="warn")
            if res.marker is not None and res.marker.trace_id:
                return {res.marker.trace_id}
    except Exception:
        pass
    return set()


# trace:exempt reason=internal-helper
def _ids_in_file(text: str) -> set[str]:
    """Every trace id present in the file text (suggestion collision guard)."""
    ids: set[str] = set()
    try:
        for hit in iter_marker_hits(text, "file"):
            res = parse_marker_hit(hit, unknown_keys="warn")
            if res.marker is not None and res.marker.trace_id:
                ids.add(res.marker.trace_id)
    except Exception:
        pass
    return ids


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:exempt reason=internal-detail
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


# trace:v1 id=impl.hooks.guidance work=WORK-TL-001
def _guidance(
    ctx: HookContext,
    path: str,
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
        work = ctx.state.active_work(ctx.session_id) if ctx.state else None
        reqs = ctx.state.active_requirements(ctx.session_id) if ctx.state else None
        req = reqs[0] if reqs and len(reqs) == 1 else None
        work_attr = f" work={work}" if work else ""
        req_attr = f" satisfies={req}" if req else ""
        example = f"# trace:v1 id=impl.<slug>{work_attr}{req_attr}"
        if is_new_file:
            lines = ["NEW ARTIFACT CREATED", ""]
            if work:
                lines.append(f"Active work item: {work}")
            if req:
                lines.append(f"Active requirement: {req}")
            lines.append("Candidates:")
            lines += [f"- {name}" for name in untraced]
            if reqs and len(reqs) > 1:
                lines.append("Candidate requirements (choose per boundary):")
                lines += [f"- {rid}" for rid in reqs]
            lines += [
                "",
                "If this file introduces a meaningful behavior boundary (public",
                "API, business rule, security boundary, persistence/migration,",
                "verification test, ...), create or reuse a trace ID and link it",
                "semantically, e.g.:",
                f"  {example}",
                "Do not trace imports, boilerplate, generated code, or trivial",
                "helpers.",
            ]
        else:
            from tracelayer.discovery.suggest import suggest_marker

            lines = ["NEW UNTRACED BEHAVIOR", ""]
            existing_ids = _ids_in_file(_read_text(ctx.project.root, path) or "")
            for name in untraced:
                file_text = _read_text(ctx.project.root, path) or ""
                b = next(
                    (x for x in _untraced_boundaries(ctx, path, file_text) if x.name == name),
                    None,
                )
                if b is not None:
                    suggestion = suggest_marker(
                        b,
                        path,
                        work=work,
                        requirement=req,
                        plan=ctx.state.active_plan(ctx.session_id) if ctx.state else None,
                        existing_ids=existing_ids,
                    )
                    lines.append(f"Add a trace marker above `{name}` ({suggestion.role}):")
                    lines.append(f"  {suggestion.marker}")
                    if suggestion.sidecar:
                        lines.append(f"  sidecar: {suggestion.sidecar}")
                else:
                    lines.append(f"Add a trace marker above `{name}`:")
                    lines.append(f"  {example}")
            if reqs and len(reqs) > 1:
                lines.append("Candidate requirements (choose the correct one per boundary):")
                lines += [f"- {rid}" for rid in reqs]
            lines.append("See the traceability skill for valid fields and formats.")
        blocks.append("\n".join(lines))
    return fit("\n\n".join(blocks), ctx.project.config.hooks.max_context_chars)
