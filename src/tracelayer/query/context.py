"""Node context queries (FR-013, §Q context.py).

``build_context`` summarizes one node: its declared upstream intent
(work / satisfies / implements / addresses / derived_from edges), its
declared downstream dependents (verifies / exercises / documents / deploys
edges), the latest verification outcome and proof level of linked tests,
staleness state, and Git provenance when available.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracelayer.evidence.freshness import proof_level
from tracelayer.git.repo import GitRepo
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore

# Upstream intent predicates in render order (spec 28.3).
_UPSTREAM_PREDICATES = ("work", "satisfies", "implements", "addresses", "derived_from")
# Downstream dependents (spec 28.3 "Verification" and §Q context.py).
_DOWNSTREAM_PREDICATES = ("verifies", "exercises", "documents", "deploys")
_PREDICATE_RANK = {p: i for i, p in enumerate(_UPSTREAM_PREDICATES + _DOWNSTREAM_PREDICATES)}
_VERIFY_PREDICATES = ("verifies", "exercises")  # predicates that link tests
_MAX_COMMITS = 50  # bound on the provenance commit list


@dataclass
class VerificationStatus:
    test_trace_id: str
    outcome: str | None  # latest recorded outcome or None
    proof_level: int
    current: bool


@dataclass
class ContextResult:
    node: Node
    upstream: list[tuple[Edge, Node]]  # declared edges OUT (intent)
    downstream: list[tuple[Edge, Node]]  # declared edges IN (dependents)
    verification: list[VerificationStatus]
    staleness: str
    provenance: dict  # first_seen/last_modified/commits; may be absent


def _framework_id_of(test_node: Node) -> str:
    """Framework test id used to look up outcomes; falls back to the trace id.

    Evidence ingest keys outcomes by the framework test id; the test node
    records it in metadata when known.
    """
    value = test_node.metadata.get("framework_test_id")
    return str(value) if value else test_node.trace_id


def build_context(
    store: GraphStore, gitrepo: GitRepo | None, trace_id: str
) -> ContextResult | None:
    """Build the context summary for ``trace_id``, or None when unknown.

    Dangling declared edges (targets missing from the store, TL002) are
    skipped deterministically; a test's verification is ``current`` when its
    latest outcome is ``pass`` and the test node itself is current.
    """
    node = store.get_node(trace_id=trace_id)
    if node is None:
        return None
    uid = node.entity_uid

    upstream: list[tuple[Edge, Node]] = []
    for edge in store.edges_from(uid):
        if edge.predicate not in _UPSTREAM_PREDICATES:
            continue
        target = store.get_node(uid=edge.to_uid)
        if target is not None:
            upstream.append((edge, target))
    upstream.sort(key=lambda p: (_PREDICATE_RANK[p[0].predicate], p[1].trace_id))

    downstream: list[tuple[Edge, Node]] = []
    for edge in store.edges_to(uid):
        if edge.predicate not in _DOWNSTREAM_PREDICATES:
            continue
        source = store.get_node(uid=edge.from_uid)
        if source is not None:
            downstream.append((edge, source))
    downstream.sort(key=lambda p: (_PREDICATE_RANK[p[0].predicate], p[1].trace_id))

    verification: list[VerificationStatus] = []
    seen_tests: set[str] = set()
    for edge, source in downstream:
        if edge.predicate not in _VERIFY_PREDICATES or source.node_type != "test":
            continue
        if source.entity_uid in seen_tests:
            continue
        seen_tests.add(source.entity_uid)
        outcome: str | None = None
        latest = store.latest_outcome(_framework_id_of(source))
        if latest is not None:
            outcome = latest.outcome
        level = proof_level(store, source.entity_uid, uid)
        current = outcome == "pass" and source.status() == "current"
        verification.append(VerificationStatus(source.trace_id, outcome, level, current))
    verification.sort(key=lambda v: v.test_trace_id)

    provenance: dict = {}
    if gitrepo is not None and node.canonical_path:
        provenance = {
            "first_seen": gitrepo.first_seen_commit(node.canonical_path),
            "last_modified": gitrepo.latest_modifying_commit(node.canonical_path),
            "commits": gitrepo.commits_touching(node.canonical_path, max_count=_MAX_COMMITS),
        }

    return ContextResult(
        node=node,
        upstream=upstream,
        downstream=downstream,
        verification=verification,
        staleness=node.status(),
        provenance=provenance,
    )


def _section_header(predicate: str, node_type: str) -> str:
    """Spec 28.3 section header for an upstream predicate; implements edges
    are labeled by their target's type (decision/plan) when possible."""
    if predicate == "implements":
        return {"decision": "Decision", "plan": "Plan"}.get(node_type, "Implements")
    if predicate == "derived_from":
        return "Derived from"
    return {"work": "Work", "satisfies": "Satisfies", "addresses": "Addresses"}.get(
        predicate, predicate.capitalize()
    )


def render_context_text(ctx: ContextResult) -> str:
    """Render the context summary in the spec 28.3 layout."""
    lines: list[str] = [ctx.node.trace_id]
    if ctx.node.canonical_path:
        location = ctx.node.canonical_path
        if ctx.node.symbol_qualified_name:
            location = f"{location}::{ctx.node.symbol_qualified_name}"
        lines.append(location)

    sections: list[tuple[str, list[str]]] = []
    for edge, target in ctx.upstream:
        header = _section_header(edge.predicate, target.node_type)
        item = target.trace_id
        if edge.predicate == "satisfies":
            item = f"{target.trace_id} [{target.status().upper()}]"
        if sections and sections[-1][0] == header:
            sections[-1][1].append(item)
        else:
            sections.append((header, [item]))
    for header, items in sections:
        lines.append("")
        lines.append(f"{header}:")
        lines.extend(f"  {item}" for item in items)

    if ctx.verification:
        lines.append("")
        lines.append("Verification:")
        for status in ctx.verification:
            outcome = str(status.outcome).upper() if status.outcome else "UNKNOWN"
            marker = "CURRENT" if status.current else "STALE"
            level = status.proof_level
            lines.append(f"  {status.test_trace_id}  {outcome}  EXECUTION=L{level}  {marker}")

    if ctx.provenance:
        lines.append("")
        lines.append("Git:")
        for key in ("first_seen", "last_modified"):
            value = ctx.provenance.get(key)
            lines.append(f"  {key}: {value if value else '<none>'}")
        commits = ctx.provenance.get("commits") or []
        if commits:
            lines.append(f"  commits: {len(commits)}")

    lines.append("")
    lines.append("Use:")
    lines.append(f"  trace impact {ctx.node.trace_id}")
    lines.append(f"  trace graph {ctx.node.trace_id} --depth 2")
    return "\n".join(lines) + "\n"
