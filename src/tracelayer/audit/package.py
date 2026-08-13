"""Bounded, deterministic audit input package construction (spec 30.2, FR-033)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracelayer.audit.schema import AUDIT_PACKAGE_SCHEMA
from tracelayer.diagnostics import SEVERITY_ERROR
from tracelayer.graph.fingerprints import semantic_fingerprint

if TYPE_CHECKING:
    from tracelayer.config import Project
    from tracelayer.git.repo import GitRepo
    from tracelayer.graph.models import Node
    from tracelayer.graph.store import GraphStore

# Line cap per excerpt keeps reads bounded even when a node claims a huge range.
MAX_EXCERPT_LINES = 400

# node_type -> package section names (spec 30.2).
_NODE_TYPES_BY_SECTION: dict[str, tuple[str, ...]] = {
    "requirements": ("requirement", "nfr"),
    "decisions": ("decision",),
    "implementations": ("implementation",),
    "tests": ("test",),
}


def build_audit_package(
    project: Project,
    store: GraphStore,
    gitrepo: GitRepo | None,
    *,
    work_id: str | None = None,
    changed_ids: set[str] | None = None,
    max_excerpt_chars: int = 2000,
    max_items: int = 50,
) -> dict:
    """Build the bounded auditor input package (spec 30.2).

    Selection is deterministic: candidates are sorted by trace_id and nodes
    linked to the work item or the changed set are listed before the remainder,
    capped at max_items per section. Excerpts are read from traced artifact
    files (canonical path + line range), stripped, and truncated to
    max_excerpt_chars. When ``changed_ids`` is None the changed set is derived
    from changed git paths mapped to traced nodes, falling back to the work
    item's one-hop neighbors when git reports nothing traced.
    """
    all_nodes = store.all_nodes(active_only=True)
    by_uid = {n.entity_uid: n for n in all_nodes}

    work_node = _select_work(store, all_nodes, work_id)
    work_uid = work_node.entity_uid if work_node else None
    changed = _changed_nodes(store, all_nodes, gitrepo, work_uid, changed_ids, max_items)
    relevant = _relevant_uids(store, work_uid, changed)

    requirements: list[dict[str, str]] = []
    for node in _prioritize(_nodes_of(all_nodes, "requirements"), relevant)[:max_items]:
        excerpt = _read_excerpt(project, node, max_excerpt_chars)
        fingerprint = node.artifact_fingerprint
        if not fingerprint and excerpt:
            fingerprint = semantic_fingerprint(excerpt)
        requirements.append(
            {"id": node.trace_id, "excerpt": excerpt, "fingerprint": fingerprint or ""}
        )

    decisions = [
        {"id": n.trace_id, "excerpt": _read_excerpt(project, n, max_excerpt_chars)}
        for n in _prioritize(_nodes_of(all_nodes, "decisions"), relevant)[:max_items]
    ]

    implementations = [
        {
            "id": n.trace_id,
            "symbol": n.symbol_qualified_name or n.title or "",
            "source_excerpt": _read_excerpt(project, n, max_excerpt_chars),
        }
        for n in _prioritize(_nodes_of(all_nodes, "implementations"), relevant)[:max_items]
    ]

    tests: list[dict[str, str]] = []
    for node in _prioritize(_nodes_of(all_nodes, "tests"), relevant)[:max_items]:
        item: dict[str, str] = {
            "id": node.trace_id,
            "source_excerpt": _read_excerpt(project, node, max_excerpt_chars),
        }
        result = _test_result(store, node)
        if result is not None:
            item["result"] = result
        tests.append(item)

    stats: dict[str, Any] = {}
    try:
        stats = store.stats()
    except Exception:
        stats = {}
    evidence_summary: dict[str, Any] = {
        "evidence_runs": int(stats.get("evidence_runs", 0) or 0),
        "diagnostics": int(stats.get("diagnostics", 0) or 0),
        "changed_artifacts": int(stats.get("changed_artifacts", 0) or 0),
    }
    revision = _git_revision(gitrepo)
    if revision:
        evidence_summary["revision"] = revision

    trace_paths = _trace_paths(
        store, by_uid, work_node.trace_id if work_node else None, implementations, max_items
    )
    unexpected = _unexpected_changes(store, gitrepo, max_items)

    deterministic_status = "pass"
    if any(d.severity == SEVERITY_ERROR for d in store.get_diagnostics()):
        deterministic_status = "fail"

    return {
        "schema": AUDIT_PACKAGE_SCHEMA,
        "work": work_node.trace_id if work_node else None,
        "deterministic_status": deterministic_status,
        "changed_nodes": changed,
        "requirements": requirements,
        "decisions": decisions,
        "implementations": implementations,
        "tests": tests,
        "evidence_summary": evidence_summary,
        "trace_paths": trace_paths,
        "unexpected_changes": unexpected,
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _nodes_of(all_nodes: list[Node], section: str) -> list[Node]:
    kinds = _NODE_TYPES_BY_SECTION[section]
    return [n for n in all_nodes if n.node_type in kinds]


def _select_work(store: GraphStore, all_nodes: list[Node], work_id: str | None) -> Node | None:
    """Resolve the work anchor: the requested ID, else the first work node.

    When ``work_id`` is given but does not exist in the store, None is returned
    (simplest deterministic behavior; the caller's ID simply anchors nothing).
    """
    if work_id is not None:
        return store.get_node(trace_id=work_id)
    works = sorted((n for n in all_nodes if n.node_type == "work"), key=lambda n: n.trace_id)
    return works[0] if works else None


def _changed_nodes(
    store: GraphStore,
    all_nodes: list[Node],
    gitrepo: GitRepo | None,
    work_uid: str | None,
    changed_ids: set[str] | None,
    max_items: int,
) -> list[str]:
    """Trace IDs of changed nodes, sorted; bounded to max_items."""
    if changed_ids is not None:
        return sorted(t for t in changed_ids if store.trace_id_exists(t))[:max_items]
    traced = {n.canonical_path: n.trace_id for n in all_nodes if n.canonical_path is not None}
    changed: list[str] = []
    if gitrepo is not None:
        try:
            for f in gitrepo.changed_files():
                if f.path in traced and traced[f.path] not in changed:
                    changed.append(traced[f.path])
        except Exception:
            changed = []
    if not changed and work_uid is not None:
        seen: set[str] = set()
        for edge in store.edges_from(work_uid):
            node = store.get_node(uid=edge.to_uid)
            if node is not None:
                seen.add(node.trace_id)
        changed = sorted(seen)
    return changed[:max_items]


def _relevant_uids(store: GraphStore, work_uid: str | None, changed: list[str]) -> set[str]:
    """Entity UIDs linked to the work item or the changed set (one hop)."""
    uids: set[str] = set()
    if work_uid is not None:
        uids.add(work_uid)
        uids.update(e.to_uid for e in store.edges_from(work_uid))
        uids.update(e.from_uid for e in store.edges_to(work_uid))
    for trace_id in changed:
        node = store.get_node(trace_id=trace_id)
        if node is not None:
            uids.add(node.entity_uid)
    return uids


def _prioritize(nodes: list[Node], relevant_uids: set[str]) -> list[Node]:
    """Deterministic ordering: work/changed-linked nodes first, then the rest."""
    related = sorted((n for n in nodes if n.entity_uid in relevant_uids), key=lambda n: n.trace_id)
    rest = sorted((n for n in nodes if n.entity_uid not in relevant_uids), key=lambda n: n.trace_id)
    return related + rest


def _read_excerpt(project: Project, node: Node, max_chars: int) -> str:
    """Bounded excerpt of the node's traced artifact, or '' when unavailable.

    Reads are confined to the project root; the line range is capped at
    MAX_EXCERPT_LINES and the resulting text at max_chars.
    """
    path = node.canonical_path
    if not path or node.source_start_line is None:
        return ""
    root = project.root.resolve()
    try:
        file_path = (root / path).resolve()
        file_path.relative_to(root)
    except (OSError, ValueError):
        return ""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    start = max(1, node.source_start_line)
    if start > len(lines):
        return ""
    end = node.source_end_line or start
    end = min(end, len(lines))
    if end - start + 1 > MAX_EXCERPT_LINES:
        end = start + MAX_EXCERPT_LINES - 1
    excerpt = "\n".join(lines[start - 1 : end]).strip()
    if len(excerpt) > max_chars:
        suffix = "\n…[truncated]"
        keep = max_chars - len(suffix)
        if keep <= 0:
            excerpt = excerpt[:max_chars]
        else:
            excerpt = excerpt[:keep].rstrip() + suffix
    return excerpt


def _test_result(store: GraphStore, node: Node) -> str | None:
    """Latest outcome for a test node; None when no evidence exists.

    Tries the store's latest-outcome lookup by trace id, then falls back to
    observed passed/failed edges leaving the node.
    """
    try:
        outcome = store.latest_outcome(node.trace_id)
    except Exception:
        outcome = None
    if outcome is not None and getattr(outcome, "outcome", None):
        return str(outcome.outcome)
    for edge in store.all_edges(status=None):
        if edge.from_uid == node.entity_uid and edge.predicate in ("passed", "failed"):
            return "pass" if edge.predicate == "passed" else "fail"
    return None


def _trace_paths(
    store: GraphStore,
    by_uid: dict[str, Node],
    work_id: str | None,
    implementations: list[dict[str, str]],
    max_items: int,
) -> list[list[str]]:
    """Per-implementation requirement -> implementation -> test paths."""
    paths: list[list[str]] = []
    for impl in implementations:
        impl_node = store.get_node(trace_id=impl["id"])
        if impl_node is None:
            continue
        path: list[str] = []
        for edge in store.edges_from(impl_node.entity_uid, "satisfies"):
            target = by_uid.get(edge.to_uid)
            if target is not None and target.trace_id not in path:
                path.append(target.trace_id)
        if impl["id"] not in path:
            path.append(impl["id"])
        tests: set[str] = set()
        for edge in store.edges_to(impl_node.entity_uid, "exercises"):
            src = by_uid.get(edge.from_uid)
            if src is not None:
                tests.add(src.trace_id)
        for edge in store.edges_from(impl_node.entity_uid, "satisfies"):
            target = by_uid.get(edge.to_uid)
            if target is None:
                continue
            for e2 in store.edges_to(target.entity_uid, "verifies"):
                src = by_uid.get(e2.from_uid)
                if src is not None:
                    tests.add(src.trace_id)
        path.extend(sorted(tests))
        if work_id is not None and work_id not in path:
            path.insert(0, work_id)
        paths.append(path)
    return paths[:max_items]


def _unexpected_changes(store: GraphStore, gitrepo: GitRepo | None, max_items: int) -> list[str]:
    """Changed paths with no traced node, sorted; [] without git."""
    if gitrepo is None:
        return []
    traced = {n.canonical_path for n in store.all_nodes(active_only=True) if n.canonical_path}
    try:
        changed = [f.path for f in gitrepo.changed_files() if f.path not in traced]
    except Exception:
        return []
    return sorted(changed)[:max_items]


def _git_revision(gitrepo: GitRepo | None) -> str | None:
    if gitrepo is None:
        return None
    try:
        return gitrepo.rev()
    except Exception:
        return None
