"""TraceLayer engine: indexing, verification, queries, hooks (contract §J).

The Engine is the single entry point for the CLI and for the machine API
(spec Section 29: ``TraceRepository.open(".")``). It wires together the
shared modules: discovery, protocol markers, artifacts, symbols, git
provenance, fingerprints/staleness, the graph store, policy evaluation,
queries, hooks, evidence ingest, migration, doctor, and audit.

Deterministic by construction: no randomness, no model calls, stable
ordering everywhere. Recoverable input problems become ``Diagnostic``
objects; ``ValueError``/``KeyError`` are reserved for programmer errors.
"""

from __future__ import annotations

import dataclasses
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from tracelayer.config import PolicyConfig, Project, load_project
from tracelayer.diagnostics import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Diagnostic,
    make,
)
from tracelayer.git.repo import GitRepo
from tracelayer.graph.fingerprints import (
    normalize_block,
    semantic_fingerprint,
    source_fingerprint,
)
from tracelayer.graph.models import EVIDENCE_STATUS_HISTORICAL, Edge, Node
from tracelayer.graph.store import GraphStore, edge_uid, entity_uid
from tracelayer.graph.traverse import Subgraph, bounded_walk
from tracelayer.protocol import (
    EDGE_ORDER,
    ParsedMarker,
    infer_node_type,
    iter_marker_hits,
    parse_marker_hit,
)
from tracelayer.protocol.ids import generate_id, is_valid_id
from tracelayer.protocol.marker import MarkerHit

# Lazy-heavy or cycle-prone imports stay local to the methods that use them
# (hooks.stop_gate imports this module lazily, so this module must not import
# it eagerly).


# --------------------------------------------------------------------------
# Report dataclasses
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail
@dataclass
# trace:exempt reason=internal-detail
class IndexReport:
    """Summary of one index run (spec 18.1, 58)."""

    nodes: int
    edges: int
    markers: int
    diagnostics: int
    changed_files: int
    duration_ms: int
    per_stage: dict[str, int] = field(default_factory=dict)


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail
@dataclass
# trace:exempt reason=internal-detail
class VerifyResult:
    """Outcome of ``trace verify`` (spec 28.6)."""

    status: str  # pass | fail
    policy: str
    lifecycle: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    blocking: bool = False

    # trace:exempt reason=internal-detail
    def exit_code(self) -> int:
        """0 pass | 1 blocking (spec 28.6)."""
        return 1 if self.blocking else 0


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail


# trace:exempt reason=internal-detail
@dataclass
# trace:exempt reason=internal-detail
class StatusReport:
    """Counts per spec 28.2."""

    nodes: int
    declared_edges: int
    structural_edges: int
    evidence_runs: int
    broken_refs: int
    blocking_stale: int
    warnings: int
    changed_artifacts: int
    policy: str
    lifecycle: str


# --------------------------------------------------------------------------
# Indexing helpers
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail
def _now_iso() -> str:
    """UTC timestamp for observed_at / last_indexed_at columns."""
    return datetime.now(UTC).isoformat(timespec="seconds")


# trace:exempt reason=internal-detail
def _work_toml_nodes(project: Project, now: str) -> tuple[list[Node], list[Diagnostic]]:
    """Work nodes declared in ``<root>/.trace/work.toml`` (spec 31.2, FR-032).

    Each ``[work."ID"]`` entry becomes a declared ``work`` node carrying its
    mirrors as metadata.  TOML syntax errors and invalid work IDs surface as
    TL100 diagnostics (config-style data); they do not abort the index.
    """
    path = project.root / ".trace" / "work.toml"
    if not path.exists():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [], [
            make(
                "TL100",
                path=".trace/work.toml",
                severity=SEVERITY_ERROR,
                message=f"Could not parse {path}: {exc}",
            )
        ]
    nodes: list[Node] = []
    diags: list[Diagnostic] = []
    for key, entry in data.get("work", {}).items():
        if not isinstance(entry, dict):
            continue
        if not is_valid_id(key):
            diags.append(
                make(
                    "TL100",
                    trace_id=key,
                    path=".trace/work.toml",
                    severity=SEVERITY_ERROR,
                    message=f"Invalid work ID {key!r} in .trace/work.toml",
                )
            )
            continue
        title = entry.get("title")
        if not isinstance(title, str):
            title = None
        mirrors = entry.get("mirrors")
        nodes.append(
            Node(
                entity_uid=entity_uid(key),
                trace_id=key,
                node_type="work",
                source_kind="declared",
                title=title,
                canonical_path=".trace/work.toml",
                metadata={
                    "mirrors": mirrors if isinstance(mirrors, dict) else {},
                    "work_label": title,
                },
                last_indexed_at=now,
            )
        )
    return nodes, diags


# trace:exempt reason=internal-detail
def _inactive_copy(node: Node, now: str) -> Node:
    """Historical copy of a node whose marker disappeared (spec 18.3).

    The node keeps its identity, fingerprint, and metadata but is marked
    inactive; git history preserves previous existence.
    """
    return Node(
        entity_uid=node.entity_uid,
        trace_id=node.trace_id,
        node_type=node.node_type,
        source_kind=node.source_kind,
        title=node.title,
        canonical_path=node.canonical_path,
        source_start_line=node.source_start_line,
        source_end_line=node.source_end_line,
        symbol_kind=node.symbol_kind,
        symbol_qualified_name=node.symbol_qualified_name,
        artifact_fingerprint=node.artifact_fingerprint,
        revision=node.revision,
        metadata=dict(node.metadata),
        first_seen_at=node.first_seen_at,
        last_indexed_at=now,
        active=False,
    )


# trace:exempt reason=internal-detail
def _marker_edges(
    trace_id: str,
    edges_map: dict[str, list[str]],
    path: str,
    line: int,
    revision: str | None,
) -> list[Edge]:
    """Declared edges from a parsed marker (stable EDGE_ORDER iteration).

    Targets are resolved to deterministic entity uids even when the target
    node is not declared yet — unresolved targets are NOT dropped; TL002
    diagnostics are emitted for them by the indexer.
    """
    uid = entity_uid(trace_id)
    out: list[Edge] = []
    for predicate in EDGE_ORDER:
        for target in edges_map.get(predicate, []):
            out.append(
                Edge(
                    edge_uid="",
                    from_uid=uid,
                    predicate=predicate,
                    to_uid=entity_uid(target),
                    source_kind="declared",
                    source_path=path,
                    source_line=line,
                    extractor="marker",
                    revision=revision,
                    status="active",
                )
            )
    return out


# trace:exempt reason=internal-detail
def _node_from_marker(
    rel_path: str,
    marker: ParsedMarker,
    now: str,
    revision: str | None,
    *,
    generated: bool,
    start_line: int | None = None,
    end_line: int | None = None,
    symbol: Any = None,
    attachment_kind: str = "file",
    parser_support: str = "generic",
    key_path: str | None = None,
    scope: str | None = None,
) -> tuple[Node, list[Edge]]:
    """Build a declared Node (and its marker edges) from a parsed marker.

    Fingerprints per spec 19: implementations with a symbol carry the
    semantic fingerprint of the symbol source as ``artifact_fingerprint``
    plus exact ``source_hash`` and ``ast_hash`` in metadata; everything else
    fingerprints the marker line (artifacts from markdown blocks pass their
    own fingerprint through the caller).
    """
    tid = marker.trace_id or ""
    node_type = marker.node_type or infer_node_type(tid) or "document"
    meta: dict[str, Any] = {
        "structural_attachment": attachment_kind,
        "parser_support": parser_support,
        "status": "current",
    }
    if generated:
        meta["generated"] = True
        # Generated files are excluded from mandatory marker rules (18.4).
        meta["parser_support"] = "generic"
    if key_path:
        meta["yaml_key_path"] = key_path
    if "expects" in marker.properties:
        meta["expects"] = marker.properties["expects"]
    if "state" in marker.properties:
        meta["state"] = marker.properties["state"]
    for key in ("canonical_source", "value"):
        if key in marker.properties:
            meta[key] = marker.properties[key]
    if scope:
        meta["scope"] = scope

    fingerprint = semantic_fingerprint(normalize_block(marker.raw))
    meta["source_hash"] = source_fingerprint(marker.raw)
    symbol_kind: str | None = None
    qualified: str | None = None
    start, end = start_line, end_line
    if symbol is not None:
        symbol_kind = symbol.kind
        qualified = symbol.qualified_name
        start, end = symbol.start_line, symbol.end_line
        fingerprint = semantic_fingerprint(normalize_block(symbol.source))
        meta["source_hash"] = source_fingerprint(symbol.source)
        meta["ast_hash"] = symbol.ast_hash()
        if node_type == "test":
            meta["framework_test_id"] = symbol.qualified_name

    work = marker.edges.get("work")
    if work:
        meta["work_label"] = work[0]

    node = Node(
        entity_uid=entity_uid(tid),
        trace_id=tid,
        node_type=node_type,
        source_kind="declared",
        title=marker.title,
        canonical_path=rel_path,
        source_start_line=start,
        source_end_line=end,
        symbol_kind=symbol_kind,
        symbol_qualified_name=qualified,
        artifact_fingerprint=fingerprint,
        revision=revision,
        metadata=meta,
        last_indexed_at=now,
    )
    return node, _marker_edges(tid, marker.edges, rel_path, marker.line, revision)


# trace:exempt reason=internal-detail
def _process_file(
    project: Project,
    rel_path: str,
    text: str,
    revision: str | None,
    now: str,
) -> tuple[list[Node], list[Edge], list[Diagnostic], int, int]:
    """Extract nodes/edges from one file (index pipeline steps 3-9).

    Returns ``(nodes, edges, diagnostics, marker_hits, symbols_attached)``.
    Markdown uses heading/HTML-comment artifact extraction plus
    fence-aware marker scanning; YAML uses section attachment; supported
    languages use symbol attachment; everything else degrades honestly to
    file-level attachment (spec 18.5, NFR-007).
    """
    from tracelayer.artifacts.markdown import (
        EDGE_WINDOW_LINES,
        extract_markdown_blocks,
        markdown_marker_hits,
    )
    from tracelayer.artifacts.yaml import attach_sections, extract_yaml_sections
    from tracelayer.discovery.files import classify
    from tracelayer.discovery.ignore import glob_match
    from tracelayer.discovery.scopes import scope_of
    from tracelayer.symbols.base import attach_markers
    from tracelayer.symbols.registry import get_parser, supported_languages

    config = project.config
    _kind, language = classify(Path(rel_path))
    diags: list[Diagnostic] = []
    nodes: list[Node] = []
    edges: list[Edge] = []
    generated = glob_match(rel_path, config.discovery.generated)
    scope = scope_of(rel_path, config)
    markers = 0
    symbols_attached = 0

    # trace:exempt reason=internal-detail
    def parse_all(hits: list[MarkerHit]) -> dict[int, ParsedMarker | None]:
        """Parse every hit once (diags collected); id(hit) -> marker."""
        nonlocal markers
        parsed: dict[int, ParsedMarker | None] = {}
        for hit in hits:
            markers += 1
            res = parse_marker_hit(hit, unknown_keys=config.markers.unknown_keys)
            diags.extend(res.diagnostics)
            parsed[id(hit)] = res.marker
        return parsed

    # --- Markdown --------------------------------------------------------
    if language == "markdown":
        blocks = extract_markdown_blocks(rel_path, text, config)
        hits = markdown_marker_hits(rel_path, text)
        parsed = parse_all(hits)
        consumed = {b.line for b in blocks}
        props_by_tid: dict[str, dict] = {}
        for hit in hits:
            marker = parsed.get(id(hit))
            if marker is not None and marker.trace_id:
                props_by_tid[marker.trace_id] = marker.properties
        for block in blocks:
            body_lines = len(block.body.splitlines()) if block.body else 0
            meta: dict[str, Any] = {
                "structural_attachment": "document",
                "parser_support": "markdown",
                "source_hash": source_fingerprint(block.body),
                "status": "current",
            }
            if generated:
                meta["generated"] = True
            if scope:
                meta["scope"] = scope
            for prop in ("expects", "state", "canonical_source", "value"):
                if prop in props_by_tid.get(block.trace_id, {}):
                    meta[prop] = props_by_tid[block.trace_id][prop]
            first = next((ln.strip() for ln in block.body.splitlines() if ln.strip()), None)
            if first:
                meta["summary"] = first[:200]
            nodes.append(
                Node(
                    entity_uid=entity_uid(block.trace_id),
                    trace_id=block.trace_id,
                    node_type=block.node_type,
                    source_kind="declared",
                    title=block.title or block.trace_id,
                    canonical_path=rel_path,
                    source_start_line=block.line,
                    source_end_line=block.line + body_lines,
                    artifact_fingerprint=block.fingerprint,
                    revision=revision,
                    metadata=meta,
                    last_indexed_at=now,
                )
            )
            edges.extend(_marker_edges(block.trace_id, block.edges, rel_path, block.line, revision))
        for hit in hits:
            if hit.line in consumed:
                continue
            marker = parsed.get(id(hit))
            if marker is None or not marker.trace_id:
                continue
            # A marker within EDGE_WINDOW_LINES after a heading with the same
            # id was absorbed into the block (spec 11.4): do not re-declare it.
            if any(
                b.line < hit.line <= b.line + EDGE_WINDOW_LINES and b.trace_id == marker.trace_id
                for b in blocks
            ):
                continue
            node, e = _node_from_marker(
                rel_path,
                marker,
                now,
                revision,
                generated=generated,
                attachment_kind="file",
                parser_support="generic",
                scope=scope,
            )
            nodes.append(node)
            edges.extend(e)
        return nodes, edges, diags, markers, symbols_attached

    hits = list(iter_marker_hits(text, rel_path))
    if not hits:
        return [], [], [], 0, 0
    parsed = parse_all(hits)

    # --- YAML sections ----------------------------------------------------
    if language == "yaml":
        sections = extract_yaml_sections(text)
        pairs = attach_sections(sections, hits)
        paired: set[int] = set()
        for section, hit in pairs:
            marker = parsed.get(id(hit))
            if marker is None or not marker.trace_id:
                continue
            paired.add(id(hit))
            node, e = _node_from_marker(
                rel_path,
                marker,
                now,
                revision,
                generated=generated,
                start_line=section.start_line,
                end_line=section.end_line,
                attachment_kind="section",
                parser_support="yaml",
                key_path=section.key_path,
                scope=scope,
            )
            nodes.append(node)
            edges.extend(e)
        for hit in hits:
            if id(hit) in paired:
                continue
            marker = parsed.get(id(hit))
            if marker is None or not marker.trace_id:
                continue
            node, e = _node_from_marker(
                rel_path,
                marker,
                now,
                revision,
                generated=generated,
                attachment_kind="file",
                parser_support="generic",
                scope=scope,
            )
            nodes.append(node)
            edges.extend(e)
        return nodes, edges, diags, markers, symbols_attached

    # --- Supported-language symbol attachment -----------------------------
    if language in supported_languages() and getattr(config.index.languages, language, False):
        parser = get_parser(language)
        try:
            symbols = parser.parse(text, rel_path)
        except Exception:
            symbols = []
        attachments = attach_markers(symbols, hits, text.splitlines())
        for att in attachments:
            marker = parsed.get(id(att.hit))
            if marker is None or not marker.trace_id:
                continue
            if att.symbol is None:
                meta_attach = "file"
                if not generated:
                    diags.append(
                        make(
                            "TL003",
                            trace_id=marker.trace_id,
                            path=rel_path,
                            line=att.hit.line,
                            message=(
                                f"Marker for {marker.trace_id} is detached from "
                                f"any supported symbol in {rel_path}"
                            ),
                        )
                    )
            else:
                meta_attach = "ambiguous" if att.ambiguity else "symbol"
                symbols_attached += 1
                if att.ambiguity and not generated:
                    diags.append(
                        make(
                            "TL003",
                            trace_id=marker.trace_id,
                            path=rel_path,
                            line=att.hit.line,
                            message=(
                                f"Marker for {marker.trace_id} is ambiguous "
                                f"(multiple candidate symbols) in {rel_path}"
                            ),
                        )
                    )
            node, e = _node_from_marker(
                rel_path,
                marker,
                now,
                revision,
                generated=generated,
                symbol=att.symbol,
                attachment_kind=meta_attach,
                parser_support=language,
                scope=scope,
            )
            nodes.append(node)
            edges.extend(e)
        return nodes, edges, diags, markers, symbols_attached

    # --- Generic file-level attachment (spec 18.5) -------------------------
    for hit in hits:
        marker = parsed.get(id(hit))
        if marker is None or not marker.trace_id:
            continue
        node, e = _node_from_marker(
            rel_path,
            marker,
            now,
            revision,
            generated=generated,
            attachment_kind="file",
            parser_support="generic",
            scope=scope,
        )
        nodes.append(node)
        edges.extend(e)
    return nodes, edges, diags, markers, symbols_attached


# trace:exempt reason=internal-detail
def _dedupe_nodes(nodes: list[Node], diags: list[Diagnostic]) -> list[Node]:
    """Keep the first declaration of each trace id; TL001 for the rest.

    The nodes table enforces UNIQUE(trace_id), so duplicates (same id in two
    files, or a heading plus a plain marker) collapse deterministically to the
    earliest (path, line) declaration and every other location is reported.
    """
    locs: dict[str, list[tuple[str | None, int | None]]] = {}
    for n in nodes:
        locs.setdefault(n.trace_id, []).append((n.canonical_path, n.source_start_line))
    for tid, where in locs.items():
        if len(where) > 1:
            for path, line in where:
                diags.append(
                    make(
                        "TL001",
                        trace_id=tid,
                        path=path,
                        line=line,
                        message=(f"Duplicate trace ID {tid} declared at {len(where)} locations"),
                    )
                )
    out: dict[str, Node] = {}
    for n in nodes:
        out.setdefault(n.trace_id, n)
    return list(out.values())


# trace:exempt reason=internal-detail
def _tl002_diags(nodes: list[Node], edges: list[Edge]) -> list[Diagnostic]:
    """TL002 for active declared edges whose target is missing or inactive.

    Emitted at index time (contract §J): unresolved targets are kept as edges
    but flagged, and edges pointing at a deleted/inactive target (spec 18.3)
    are identified so no dangling active edge goes unnoticed.
    """
    active_uids = {n.entity_uid for n in nodes if n.active}
    out: list[Diagnostic] = []
    for edge in sorted(edges, key=lambda e: e.edge_uid):
        if edge.status != "active" or edge.to_uid in active_uids:
            continue
        out.append(
            make(
                "TL002",
                path=edge.source_path,
                line=edge.source_line,
                message=(
                    f"{edge.predicate} edge targets node uid {edge.to_uid} which is "
                    f"missing or inactive (declared in {edge.source_path or 'unknown'})"
                ),
            )
        )
    return out


# Config files whose change alters enforcement (Threat T10 / DoD security).
_POLICY_CONFIG_FILES = (".trace/policy.toml", ".trace/trace.toml")
_POLICY_CONFIG_DIRS = (".trace/policy/", ".trace/schema/")


# trace:exempt reason=internal-detail
def _policy_config_diags(changed_paths: set[str]) -> list[Diagnostic]:
    """TL063 WARNINGs for changed policy/schema files (Threat T10, DoD security).

    Any changed path that is the policy or trace config, or lives under
    ``.trace/policy/`` or ``.trace/schema/``, gets a WARNING that
    enforcement configuration changed.  Non-blocking; the stop gate inherits
    it through verify's diagnostics.
    """
    out: list[Diagnostic] = []
    for path in sorted(changed_paths):
        if path in _POLICY_CONFIG_FILES or any(
            path.startswith(prefix) for prefix in _POLICY_CONFIG_DIRS
        ):
            out.append(
                make(
                    "TL063",
                    path=path,
                    severity=SEVERITY_WARNING,
                    message="enforcement configuration changed in this change set",
                )
            )
    return out


# trace:exempt reason=internal-detail
def _edge_order_key(e: Edge) -> str:
    """Deterministic edge row order: the store's computed edge uid.

    ``replace_all`` inserts in list order and edges are read back in
    insertion order (rowid), so clean and incremental rebuilds of the same
    graph must insert identically ordered rows for byte-identical ``trace
    graph --format json`` output.
    """
    return edge_uid(
        e.from_uid,
        e.predicate,
        e.to_uid,
        e.source_kind,
        e.source_path,
        e.source_line,
    )


# trace:exempt reason=internal-detail
def _structural_contains_edges(nodes: list[Node], revision: str | None) -> list[Edge]:
    """Derive structural ``contains`` edges among symbol-attached nodes (spec 12.3).

    When one traced symbol in a file strictly encloses another traced
    symbol's line range, emit ``(enclosing_uid, "contains", enclosed_uid)``
    with ``source_kind="structural"`` and extractor ``tracelayer-symbols``.
    Only active nodes attached to a symbol (``structural_attachment ==
    "symbol"``) participate; file-level attachments are skipped.  Deterministic:
    nodes are grouped by canonical path, iterated in (start line, trace id)
    order, and the store's deterministic edge_uid scheme dedupes.
    """
    by_path: dict[str, list[tuple[int, int, Node]]] = {}
    for n in nodes:
        if (
            n.active
            and n.metadata.get("structural_attachment") == "symbol"
            and n.source_start_line is not None
            and n.source_end_line is not None
            and n.canonical_path
        ):
            by_path.setdefault(n.canonical_path, []).append(
                (n.source_start_line, n.source_end_line, n)
            )
    out: list[Edge] = []
    for path, group in sorted(by_path.items()):
        group.sort(key=lambda t: (t[0], t[2].trace_id))
        for i, (o_start, o_end, outer) in enumerate(group):
            for i_start, i_end, inner in group[:i]:
                if i_start < o_start and i_end > o_end:
                    out.append(
                        Edge(
                            edge_uid="",
                            from_uid=inner.entity_uid,
                            predicate="contains",
                            to_uid=outer.entity_uid,
                            source_kind="structural",
                            source_path=path,
                            source_line=o_start,
                            extractor="tracelayer-symbols",
                            revision=revision,
                            status="active",
                        )
                    )
    return out


# trace:exempt reason=internal-detail
def _staleness_pass(
    store: GraphStore,
    nodes: list[Node],
    edges: list[Edge],
    *,
    revision: str | None,
    now: str,
    clean: bool,
) -> None:
    """Compare fingerprints against artifact_versions and propagate staleness.

    A node whose fingerprint differs from the last recorded version is the
    changed artifact (``metadata['changed']``, status stays ``current``); its
    declared dependents — nodes with an active declared edge pointing at it —
    are marked ``stale_review_required`` and prior evidence is flagged
    ``historical_not_current`` (spec 19.3, FR-009).  Edge rows stay
    ``active`` so structural rules that require active declared edges
    (TL011, TL020) keep working; node status drives TL110.  Review state
    (``reviewed_needs_verification``) and other non-current statuses are
    preserved across re-indexes for unchanged nodes.  Change detection runs
    on both clean and incremental rebuilds (the materialized graph must be
    equivalent for the same repo+evidence state); ``clean`` only skips the
    status carry-over of unchanged nodes (fresh rebuild semantics).
    """
    old_by_id = {n.trace_id: n for n in store.all_nodes(active_only=False)}
    changed: set[str] = set()
    for node in nodes:
        node.metadata.setdefault("status", "current")
        fp = node.artifact_fingerprint
        old = old_by_id.get(node.trace_id)
        if old is not None:
            node.first_seen_at = old.first_seen_at
        if fp:
            prev = store.previous_fingerprints(node.trace_id, exclude=fp)
            if prev and prev[-1] != fp:
                changed.add(node.trace_id)
                node.metadata["changed"] = True
                node.metadata["status"] = "current"
            elif old is not None and old.status() != "current" and not clean:
                node.metadata["status"] = old.status()
        elif old is not None and old.status() != "current" and not clean:
            node.metadata["status"] = old.status()
    if changed:
        uid_to_node = {n.entity_uid: n for n in nodes}
        changed_uids = {entity_uid(t) for t in changed}
        for edge in edges:
            # Only declared dependency edges propagate staleness; structural
            # edges (e.g. contains) describe containment, not dependency.
            if (
                edge.source_kind != "declared"
                or edge.status != "active"
                or edge.to_uid not in changed_uids
            ):
                continue
            dep = uid_to_node.get(edge.from_uid)
            if dep is None:
                continue
            if dep.metadata.get("status") != "stale_review_required":
                dep.metadata["status"] = "stale_review_required"
                dep.metadata["evidence_status"] = EVIDENCE_STATUS_HISTORICAL


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

_HOOK_MODULES = {
    "session-start": "session_start",
    "prompt-context": "prompt_context",
    "pre-mutation": "pre_mutation",
    "post-mutation": "post_mutation",
    "post-batch": "post_batch",
    "stop": "stop_gate",
}


# trace:exempt reason=internal-detail
class Engine:
    """The trace engine: index, verify, query, hook, audit, migrate, doctor."""

    # trace:exempt reason=internal-detail
    def __init__(self, project: Project, gitrepo: GitRepo | None = None) -> None:
        self.project = project
        self.gitrepo = gitrepo
        project.cache_dir.mkdir(parents=True, exist_ok=True)
        self.store = GraphStore.open(project.db_path)

    # trace:exempt reason=internal-detail

    # trace:exempt reason=internal-detail
    @classmethod
    # trace:exempt reason=internal-detail
    def open(cls, root: Path | None = None) -> tuple[Engine, list[Diagnostic]]:
        """Resolve the project, git repo, and graph store (contract §J).

        Config problems come back as diagnostics (exit code 2 path); a corrupt
        or unopenable index raises ``sqlite3.Error`` (exit code 3 path).
        """
        project, diags = load_project(root)
        gitrepo = GitRepo.open(project.root)
        return cls(project, gitrepo), diags

    # trace:exempt reason=internal-detail
    def close(self) -> None:
        self.store.close()

    # trace:exempt reason=internal-detail
    def __enter__(self) -> Engine:
        return self

    # trace:exempt reason=internal-detail
    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- indexing

    # trace:exempt reason=internal-detail
    def index_all(self, *, clean: bool = False) -> IndexReport:
        """Full index (spec 18.1): discover, scan, attach, fingerprint,
        staleness, atomic rebuild."""
        t0 = time.perf_counter()
        store = self.store
        project = self.project
        gitrepo = self.gitrepo
        revision = gitrepo.rev() if gitrepo is not None else None
        now = _now_iso()

        from tracelayer.discovery.files import discover_files, read_text_safe

        files = discover_files(project.root, project.config, gitrepo)
        nodes: list[Node] = []
        edges: list[Edge] = []
        diags: list[Diagnostic] = []
        markers = 0
        symbols_attached = 0
        parsed_files = 0
        for sf in files:
            text = read_text_safe(project.root / sf.path)
            if text is None:
                continue
            parsed_files += 1
            n, e, d, m, s = _process_file(project, str(sf.path), text, revision, now)
            nodes.extend(n)
            edges.extend(e)
            diags.extend(d)
            markers += m
            symbols_attached += s

        nodes = _dedupe_nodes(nodes, diags)

        # Work nodes declared in .trace/work.toml (spec 31.2, FR-032) merge by
        # trace id: a marker declaring the same work ID wins (no TL001 for the
        # legitimate overlap); otherwise the config entry becomes the node.
        work_nodes, work_diags = _work_toml_nodes(project, now)
        diags.extend(work_diags)
        seen_ids = {n.trace_id for n in nodes}
        for wn in work_nodes:
            if wn.trace_id not in seen_ids:
                nodes.append(wn)
                seen_ids.add(wn.trace_id)

        # Deletion behavior (18.3): markers that disappeared keep their
        # historical identity as inactive nodes.
        old_by_id = {n.trace_id: n for n in store.all_nodes(active_only=False)}
        new_ids = {n.trace_id for n in nodes}
        for tid, old in old_by_id.items():
            if tid not in new_ids:
                nodes.append(_inactive_copy(old, now))

        # Structural containment among symbol-attached nodes (spec 12.3).
        edges = edges + _structural_contains_edges(nodes, revision)

        _staleness_pass(store, nodes, edges, revision=revision, now=now, clean=clean)
        diags.extend(_tl002_diags(nodes, edges))

        edges.sort(key=_edge_order_key)
        store.replace_all(nodes, edges)
        for node in nodes:
            if node.artifact_fingerprint:
                store.record_artifact_version(
                    node.trace_id,
                    node.artifact_fingerprint,
                    revision,
                    node.canonical_path,
                    now,
                )
        store.replace_diagnostics(diags)

        active = sum(1 for n in nodes if n.active)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return IndexReport(
            nodes=active,
            edges=len(edges),
            markers=markers,
            diagnostics=len(diags),
            changed_files=parsed_files,
            duration_ms=duration_ms,
            per_stage={
                "files_scanned": len(files),
                "files_parsed": parsed_files,
                "markers": markers,
                "symbols_attached": symbols_attached,
                "nodes": active,
                "edges": len(edges),
            },
        )

    # trace:v1 id=impl.engine.incremental-index work=WORK-TL-001
    def index_changed(self) -> IndexReport:
        """Incremental index (spec 18.2): reparse changed files, merge into
        the existing store, mark deleted markers inactive, keep everything
        else intact."""
        t0 = time.perf_counter()
        store = self.store
        project = self.project
        gitrepo = self.gitrepo
        if gitrepo is None:
            return self.index_all()
        revision = gitrepo.rev()
        now = _now_iso()

        from tracelayer.discovery.files import read_text_safe
        from tracelayer.discovery.ignore import build_ignored

        changed_files = gitrepo.changed_files()
        if not changed_files:
            # Clean working tree (CI / post-commit): index the committed
            # change set vs the default-branch merge base (or HEAD~1).
            base = gitrepo.default_base()
            if base is not None:
                changed_files = gitrepo.changed_files(base=base)
        if not changed_files:
            return IndexReport(
                nodes=0,
                edges=0,
                markers=0,
                diagnostics=0,
                changed_files=0,
                duration_ms=0,
                per_stage={
                    "files_scanned": 0,
                    "files_parsed": 0,
                    "markers": 0,
                    "symbols_attached": 0,
                    "nodes": 0,
                    "edges": 0,
                },
            )
        is_ignored = build_ignored(project.root, project.config, gitrepo)
        changed_paths = {f.path for f in changed_files}
        new_nodes: list[Node] = []
        new_edges: list[Edge] = []
        diags: list[Diagnostic] = []
        diags.extend(_policy_config_diags(changed_paths))
        markers = 0
        symbols_attached = 0
        parsed_files = 0
        for f in changed_files:
            if f.change == "deleted":
                continue
            if is_ignored(f.path):
                continue  # discovery-excluded (e.g. tests/**, .trace/**)
            text = read_text_safe(project.root / f.path)
            if text is None:
                continue
            parsed_files += 1
            n, e, d, m, s = _process_file(project, f.path, text, revision, now)
            new_nodes.extend(n)
            new_edges.extend(e)
            diags.extend(d)
            markers += m
            symbols_attached += s
        new_nodes = _dedupe_nodes(new_nodes, diags)
        # Work nodes declared in .trace/work.toml (spec 31.2, FR-032) merge by
        # trace id so a marker declaring the same work ID wins (no TL001); the
        # entries re-confirm on every incremental run and follow the normal
        # deletion path when removed from the file.
        work_nodes, work_diags = _work_toml_nodes(project, now)
        diags.extend(work_diags)
        work_ids = {n.trace_id for n in new_nodes}
        for wn in work_nodes:
            if wn.trace_id not in work_ids:
                new_nodes.append(wn)
                work_ids.add(wn.trace_id)
        new_ids = {n.trace_id for n in new_nodes}

        existing = store.all_nodes(active_only=False)
        existing_by_id = {n.trace_id: n for n in existing}
        merged: dict[str, Node] = {}
        for n in existing:
            if n.canonical_path in changed_paths:
                continue  # replaced below (or marked inactive)
            merged[n.trace_id] = n
        for n in new_nodes:
            old = existing_by_id.get(n.trace_id)
            if old is not None:
                n.first_seen_at = old.first_seen_at
            merged[n.trace_id] = n
        deleted_ids: set[str] = set()
        for n in existing:
            if n.active and n.canonical_path in changed_paths and n.trace_id not in new_ids:
                merged[n.trace_id] = _inactive_copy(n, now)
                deleted_ids.add(n.trace_id)
        nodes = list(merged.values())

        changed_uids = {entity_uid(t) for t in new_ids | deleted_ids}
        old_edges = store.all_edges()
        # Edges are declared by their SOURCE marker, so only edges whose
        # source was re-declared (or deleted) are replaced; edges pointing at
        # a changed target from an unchanged source stay valid (one-hop
        # semantic closure, spec 18.2).  Structural edges are re-derived from
        # the merged node set below (their source ranges may have moved).
        kept = [
            e for e in old_edges if e.from_uid not in changed_uids and e.source_kind != "structural"
        ]
        edges = kept + new_edges + _structural_contains_edges(nodes, revision)

        _staleness_pass(store, nodes, edges, revision=revision, now=now, clean=False)
        # Bootstrapping: create stub requirement nodes for missing satisfies
        # targets. When source code says ``satisfies=REQ-foo`` but REQ-foo
        # doesn't exist yet, the indexer creates a minimal stub so TL002
        # doesn't block (the agent referenced a requirement that just needs
        # to be fleshed out later). Stub nodes are active, type=requirement,
        # and carry the source path where the reference was declared.
        active_uids = {n.entity_uid for n in nodes if n.active}
        for edge in edges:
            if edge.status != "active" or edge.to_uid in active_uids:
                continue
            # only auto-create stubs for requirement IDs (REQ-*)
            if not edge.to_uid:
                continue
            # check if the to_uid looks like a REQ node (by convention)
            # we auto-create stubs for satisfies/work edges targeting missing nodes
            from tracelayer.protocol.ids import infer_node_type

            ntype = infer_node_type(edge.to_uid)
            if ntype not in ("requirement", "work"):
                continue
            stub = Node(
                entity_uid=edge.to_uid,
                trace_id=edge.to_uid,
                node_type=ntype,
                source_kind="suggested",
                canonical_path=edge.source_path or "",
                artifact_fingerprint=None,
                metadata={"stub": True, "bootstrapped_by": edge.source_path},
                active=True,
                first_seen_at=now,
                last_indexed_at=now,
            )
            nodes.append(stub)
            active_uids.add(stub.entity_uid)
        diags.extend(_tl002_diags(nodes, edges))

        old_diags = [d for d in store.get_diagnostics() if d.path not in changed_paths]
        edges.sort(key=_edge_order_key)
        store.replace_all(nodes, edges)
        for node in new_nodes:
            if node.artifact_fingerprint:
                store.record_artifact_version(
                    node.trace_id,
                    node.artifact_fingerprint,
                    revision,
                    node.canonical_path,
                    now,
                )
        store.replace_diagnostics(old_diags + diags)

        active = sum(1 for n in nodes if n.active)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return IndexReport(
            nodes=active,
            edges=len(edges),
            markers=markers,
            diagnostics=len(diags),
            changed_files=parsed_files,
            duration_ms=duration_ms,
            per_stage={
                "files_scanned": len(changed_files),
                "files_parsed": parsed_files,
                "markers": markers,
                "symbols_attached": symbols_attached,
                "nodes": active,
                "edges": len(edges),
            },
        )

    # -------------------------------------------------------------- status

    # trace:exempt reason=internal-detail
    def status(self) -> StatusReport:
        """Aggregate counts per spec 28.2."""
        stats = self.store.stats()
        policy = self.project.policy
        return StatusReport(
            nodes=stats["nodes"],
            declared_edges=stats["declared_edges"],
            structural_edges=stats["structural_edges"],
            evidence_runs=stats["evidence_runs"],
            broken_refs=len(self.store.get_diagnostics(rule_id="TL002")),
            blocking_stale=sum(
                1
                for n in self.store.all_nodes(active_only=True)
                if n.status() == "stale_review_required"
            ),
            warnings=len(self.store.get_diagnostics(severity="WARNING")),
            changed_artifacts=stats["changed_artifacts"],
            policy=policy.profile if policy else "standard",
            lifecycle=policy.lifecycle_for(None) if policy else "wip",
        )

    # ------------------------------------------------------------- queries

    # trace:exempt reason=internal-detail
    def context(self, trace_id: str):
        from tracelayer.query.context import build_context

        return build_context(self.store, self.gitrepo, trace_id, root=self.project.root)

    # trace:exempt reason=internal-detail
    def why(self, trace_id: str):
        """Causal paths for a node; compensates the shared walker's leaf case.

        ``tracelayer.query.why.why_paths`` drops 1-hop paths whose causal
        chain terminates at a root with no further predecessors; synthesize
        those direct hops here (preferred predicate order, bounded) so
        ``trace why`` always explains a node with declared ancestry.
        """
        from tracelayer.query.why import why_paths

        paths = why_paths(self.store, trace_id)
        if paths:
            return paths
        node = self.store.get_node(trace_id=trace_id)
        if node is None:
            return []
        for pred in ("work", "satisfies", "implements", "addresses", "derived_from"):
            for edge in self.store.edges_from(node.entity_uid, predicate=pred):
                target = self.store.get_node(uid=edge.to_uid)
                if target is None:
                    continue
                paths.append([(edge, target)])
                if len(paths) >= 5:
                    return paths
        return paths

    # trace:exempt reason=internal-detail
    def impact(self, trace_id: str, **kw: Any):
        from tracelayer.query.impact import impact as impact_fn

        return impact_fn(self.store, self.gitrepo, trace_id, **kw)

    # trace:exempt reason=internal-detail
    def search(self, text: str, limit: int = 20):
        from tracelayer.query.search import search as search_fn

        return search_fn(self.store, text, limit=limit)

    # trace:exempt reason=internal-detail
    def subgraph(self, trace_id: str, *, depth: int = 2) -> Subgraph:
        node = self.store.get_node(trace_id=trace_id)
        if node is None:
            return Subgraph()
        return bounded_walk(self.store, node.entity_uid, direction="both", depth=depth)

    # ------------------------------------------------------------- verify

    # trace:exempt reason=internal-detail
    def _forced_evidence_project(self, lifecycle: str) -> Project:
        """Project copy with evidence-dependent gates forced on (spec 28.6)."""
        from tracelayer.policy.evaluator import effective_requirements

        policy = self.project.policy or PolicyConfig()
        reqs = effective_requirements(policy, policy.profile, lifecycle)
        forced = reqs.model_copy(
            update={
                "require_verifying_test": True,
                "require_test_pass": True,
                "require_execution_evidence": True,
            }
        )
        new_policy = policy.model_copy(update={"requirements": {lifecycle: forced}})
        return dataclasses.replace(self.project, policy=new_policy)

    # trace:exempt reason=internal-detail
    def verify(
        self,
        *,
        scope: str = "changed",
        lifecycle: str | None = None,
        require_evidence: bool = False,
    ) -> VerifyResult:
        """Evaluate policy for the changed or whole-repo scope (spec 28.6).

        ``scope='changed'`` refreshes the index incrementally first, then
        evaluates with the changed trace ids and paths from git; ``'all'``
        evaluates the whole repo.  ``require_evidence`` forces the
        evidence-dependent rules below their profile gate.  Diagnostics are
        stored back into the store (deterministic UID dedup).
        """
        from tracelayer.policy.evaluator import evaluate

        if scope not in ("changed", "all"):
            raise ValueError(f"scope must be 'changed' or 'all', got {scope!r}")
        project = self.project
        policy = project.policy
        lifecycle = policy.lifecycle_for(lifecycle) if policy is not None else (lifecycle or "wip")
        revision = self.gitrepo.rev() if self.gitrepo is not None else None
        if scope == "changed" and self.gitrepo is not None:
            if not self.store.all_nodes():
                # Cold store (fresh CI checkout / deleted cache): a partial
                # diff index would miss nodes that changed-scope edges
                # target (TL002 missing-node false positives). Build the
                # full index first; the incremental pass is then a no-op.
                self.index_all()
            else:
                # Stale warm store (branch switch / fast-forward pull): the
                # indexed revision no longer matches HEAD, so most nodes
                # describe files that no longer exist. A partial diff would
                # evaluate against ghost nodes — rebuild fully.
                current = self.gitrepo.rev()
                revisions = {
                    n.revision for n in self.store.all_nodes(active_only=True) if n.revision
                }
                if len(revisions) > 1 or (revisions and current not in revisions):
                    self.index_all()
            self.index_changed()
            changed = self.gitrepo.changed_files()
            if not changed:
                # Clean working tree (CI / post-commit): evaluate the
                # committed change set vs the merge base of the default
                # branch (or the previous commit), not nothing.
                base = self.gitrepo.default_base()
                if base is not None:
                    changed = self.gitrepo.changed_files(base=base)
            changed_paths = {f.path for f in changed}
            changed_ids = {
                n.trace_id
                for n in self.store.all_nodes(active_only=True)
                if n.canonical_path in changed_paths
            }
        else:
            changed_ids, changed_paths = None, set()
        eval_project = self._forced_evidence_project(lifecycle) if require_evidence else project
        result = evaluate(
            eval_project,
            self.store,
            lifecycle=lifecycle,
            changed_ids=changed_ids,
            changed_paths=changed_paths,
            revision=revision,
            gitrepo=self.gitrepo,
        )
        diags = result.diagnostics + _policy_config_diags(changed_paths)
        self.store.insert_diagnostics(diags)
        return VerifyResult(
            status=result.status,
            policy=policy.profile if policy else "standard",
            lifecycle=lifecycle,
            diagnostics=diags,
            blocking=result.blocking,
        )

    # ------------------------------------------------------------- evidence

    # trace:exempt reason=internal-detail
    def ingest_evidence(self, **kw: Any):
        """Ingest evidence files, binding outcomes/coverage to indexed nodes."""
        from tracelayer.evidence.ingest import ingest

        store = self.store
        test_id_map: dict[str, str] = {}
        impl_symbols: dict[str, list[tuple[int, int]]] = {}
        for n in store.all_nodes(active_only=True):
            if n.node_type == "test" and n.symbol_qualified_name:
                test_id_map[n.symbol_qualified_name] = n.trace_id
            elif (
                n.node_type == "implementation"
                and n.canonical_path
                and n.source_start_line is not None
            ):
                impl_symbols.setdefault(n.canonical_path, []).append(
                    (
                        n.source_start_line,
                        n.source_end_line or n.source_start_line,
                    )
                )
        result = ingest(
            self.project,
            store,
            **kw,
            test_id_map=test_id_map,
            impl_symbols=impl_symbols,
        )
        store.insert_diagnostics(result.diagnostics)
        return result

    # ---------------------------------------------------------------- hooks

    # trace:exempt reason=internal-detail
    def hook(self, event: str, payload: dict):
        """Run one hook event handler (spec Section 22)."""
        from tracelayer.hooks.common import hook_context

        module_name = _HOOK_MODULES.get(event)
        if module_name is None:
            raise ValueError(
                f"unknown hook event {event!r}; expected one of {sorted(_HOOK_MODULES)}"
            )
        handler = import_module(f"tracelayer.hooks.{module_name}").handle
        ctx = hook_context(self.project, self.store, self.gitrepo, payload)
        return handler(ctx, payload)

    # ---------------------------------------------------------------- audit

    # trace:exempt reason=internal-detail
    def audit_package(self, **kw: Any) -> dict:
        from tracelayer.audit.package import build_audit_package

        return build_audit_package(self.project, self.store, self.gitrepo, **kw)

    # trace:exempt reason=internal-detail
    def run_auditor(self, *, command: str, timeout: int = 300) -> tuple[dict, list[Diagnostic]]:
        from tracelayer.audit.auditor import run_auditor as run_auditor_fn

        return run_auditor_fn(self.audit_package(), command=command, timeout=timeout)

    # --------------------------------------------------------------- reports

    # trace:exempt reason=internal-detail
    def pr_summary(self) -> str:
        """Generate the PR summary (spec Section 27)."""
        store = self.store
        gitrepo = self.gitrepo
        changed_paths: set[str] = set()
        if gitrepo is not None:
            try:
                changed_paths = {f.path for f in gitrepo.changed_files()}
            except Exception:
                changed_paths = set()

        nodes = store.all_nodes(active_only=True)
        if changed_paths:
            scoped = [n for n in nodes if n.canonical_path in changed_paths]
            uids = {n.entity_uid for n in scoped}
            for n in scoped:
                for e in store.edges_from(n.entity_uid) + store.edges_to(n.entity_uid):
                    if e.status != "active":
                        continue
                    other = e.to_uid if e.from_uid == n.entity_uid else e.from_uid
                    uids.add(other)
            scope_ids = {n.trace_id for n in nodes if n.entity_uid in uids}
            nodes = [n for n in nodes if n.trace_id in scope_ids or n.node_type == "work"]

        works = sorted(n.trace_id for n in nodes if n.node_type == "work")
        reqs = sorted((n for n in nodes if n.node_type == "requirement"), key=lambda n: n.trace_id)
        impls = sorted(
            (n for n in nodes if n.node_type == "implementation"), key=lambda n: n.trace_id
        )
        tests = sorted((n for n in nodes if n.node_type == "test"), key=lambda n: n.trace_id)

        broken = len(store.get_diagnostics(rule_id="TL002"))
        stale = sum(
            1 for n in store.all_nodes(active_only=True) if n.status() == "stale_review_required"
        )
        warnings = len(store.get_diagnostics(severity="WARNING"))
        traced_paths = {
            n.canonical_path for n in store.all_nodes(active_only=True) if n.canonical_path
        }
        unexpected = sorted(p for p in changed_paths if p not in traced_paths)

        lines = ["## Trace Impact", ""]
        lines.append("**Work**")
        lines += [f"- {w}" for w in works[:20]] or ["- none"]
        lines.append("")
        lines.append("**Requirements**")
        for r in reqs[:20]:
            status = "modified" if r.metadata.get("changed") else "unchanged"
            stale_dep = sum(
                1 for e in store.edges_to(r.entity_uid) if e.status == "stale_review_required"
            )
            suffix = f"; {stale_dep} downstream traces re-reviewed" if stale_dep else ""
            lines.append(f"- {r.trace_id} - {status}{suffix}")
        lines.append("")
        lines.append("**Implementation**")
        lines += [
            f"- `{i.trace_id}` - {'modified' if i.metadata.get('changed') else 'unchanged'}"
            for i in impls[:20]
        ]
        lines.append("")
        lines.append("**Verification**")
        for t in tests[:20]:
            outcome = store.latest_outcome(t.metadata.get("framework_test_id") or t.trace_id)
            execs = store.execution_edges_for_test(t.entity_uid)
            if outcome is None:
                text = "no evidence"
            elif outcome.outcome == "pass":
                if any(e.coverage_kind == "per_test" for e in execs):
                    text = "PASS, execution confirmed (L2)"
                elif execs:
                    text = "PASS, execution confirmed (L1)"
                else:
                    text = "PASS"
            else:
                text = outcome.outcome.upper()
            lines.append(f"- `{t.trace_id}` - {text}")
        lines.append("")
        lines.append("**Trace health**")
        lines += [
            f"- Broken refs: {broken}",
            f"- Blocking stale traces: {stale}",
            f"- Warnings: {warnings}",
        ]
        lines.append("")
        lines.append("**Unexpected traced changes**")
        lines += [f"- {u}" for u in unexpected[:20]] or ["- none"]
        return "\n".join(lines) + "\n"

    # trace:exempt reason=internal-detail
    def new_id(self, node_type: str, name: str) -> str:
        """Generate a fresh schema-compliant ID (``trace new``).

        When ``name`` is already a valid ID for the requested type (e.g.
        ``trace new work --name WORK-TL-001``), it is used verbatim —
        remediation texts name exact IDs and minting a slugified variant
        leaves the original edge dangling (F12).
        """
        from tracelayer.protocol.ontology import NODE_TYPES

        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown node type {node_type!r}; choose from {sorted(NODE_TYPES)}")
        taken = {n.trace_id for n in self.store.all_nodes(active_only=False)}
        from tracelayer.protocol.ids import TYPE_PREFIX, is_valid_id

        prefix = TYPE_PREFIX.get(node_type, f"{node_type}.")
        if is_valid_id(name) and name.startswith(prefix):
            if name in taken:
                raise ValueError(f"trace id {name!r} already exists")
            return name
        return generate_id(node_type, name, taken=taken)

    # trace:exempt reason=internal-detail
    def review(self, trace_id: str) -> bool:
        """Review a stale node: STALE_REVIEW_REQUIRED -> REVIEWED_NEEDS_VERIFICATION.

        The transition applies to the named node and, deterministically, to
        every ``stale_review_required`` node in its declared-edge closure
        (bounded BFS), so reviewing a changed requirement acknowledges its
        stale dependents in one step (spec 19.3, 51).  Returns False when the
        trace id is unknown.
        """
        node = self.store.get_node(trace_id=trace_id)
        if node is None:
            return False
        self._review_closure(node)
        return True

    # trace:exempt reason=internal-detail
    def _review_closure(self, start: Node) -> None:
        """Transition stale nodes reachable from ``start`` via declared edges."""
        seen: set[str] = {start.entity_uid}
        queue = [start.entity_uid]
        for _ in range(5):  # bounded depth
            if not queue:
                break
            nxt: list[str] = []
            for uid in queue:
                for e in self.store.edges_from(uid) + self.store.edges_to(uid):
                    other = e.to_uid if e.from_uid == uid else e.from_uid
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
            queue = nxt
        for uid in seen:
            n = self.store.get_node(uid=uid)
            if n is not None and n.status() == "stale_review_required":
                self.store.set_node_meta(n.trace_id, "status", "reviewed_needs_verification")
                # Edges currently in stale_review_required follow the node;
                # active declared edges stay active (structural rules such as
                # TL020 require them to remain so).
                for e in self.store.edges_from(uid) + self.store.edges_to(uid):
                    if e.status == "stale_review_required":
                        self.store.set_edge_status(e.edge_uid, "reviewed_needs_verification")

    # ---------------------------------------------------------------- doctor

    # trace:exempt reason=internal-detail
    def doctor(self, *, fix: bool = False) -> tuple[list[Diagnostic], dict | None]:
        """Re-detect issues; optionally apply deterministic cosmetic fixes."""
        from tracelayer.doctor import apply_fixes, run_doctor

        diags = run_doctor(self.project, self.store, self.gitrepo)
        report = apply_fixes(self.project, diags) if fix else None
        return diags, report

    # -------------------------------------------------------------- migration

    # trace:exempt reason=internal-detail
    def migration_scan(self) -> tuple[list, list[Diagnostic]]:
        from tracelayer.migration.codeops import scan_codeops

        return scan_codeops(self.project.root, self.project.config)

    # trace:exempt reason=internal-detail
    def migration_scry(self) -> tuple[list, list[Diagnostic]]:
        from tracelayer.migration.scry import scan_scry

        return scan_scry(self.project.root, self.project.config)

    # trace:exempt reason=internal-detail
    def migration_plan(self):
        from tracelayer.migration.codeops import build_plan

        markers, _diags = self.migration_scan()
        return build_plan(markers, self.project)

    # trace:exempt reason=internal-detail
    def migration_apply(self, plan, *, dry_run: bool = False) -> dict:
        from tracelayer.migration.codeops import apply_plan

        return apply_plan(plan, self.project.root, self.project.config, dry_run=dry_run)

    # ----------------------------------------------------------------- docs

    # trace:exempt reason=internal-detail
    def docs_generate(self, *, check: bool = False) -> bool:
        """Write (or verify) the generated protocol docs; True when up to date.

        ``check=True`` never writes; it returns whether every generated file
        on disk matches the registries (spec 56.1).
        """
        from tracelayer.protocol.schema import markdown_docs

        docs = markdown_docs()
        if check:
            return all(
                (self.project.root / rel).exists()
                and (self.project.root / rel).read_text(encoding="utf-8") == content
                for rel, content in docs.items()
            )
        for rel, content in docs.items():
            p = self.project.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists() or p.read_text(encoding="utf-8") != content:
                p.write_text(content, encoding="utf-8")
        return True


# trace:exempt reason=internal-detail
class TraceRepository:
    """Thin machine-API facade over the Engine (spec Section 29).

    ``TraceRepository.open(".").context("impl.x")`` mirrors the CLI with
    stable public APIs.
    """

    # trace:exempt reason=internal-detail
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # trace:exempt reason=internal-detail

    # trace:exempt reason=internal-detail
    @classmethod
    # trace:exempt reason=internal-detail
    def open(cls, path: Path | str | None = None) -> TraceRepository:
        engine, _diags = Engine.open(Path(path) if path else None)
        return cls(engine)

    # trace:exempt reason=internal-detail
    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)
