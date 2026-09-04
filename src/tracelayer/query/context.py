"""Node context queries (FR-013, §Q context.py).

``build_context`` summarizes one node: its declared upstream intent
(work / satisfies / implements / addresses / derived_from edges), its
declared downstream dependents (verifies / exercises / documents / deploys
edges), the latest verification outcome and proof level of linked tests,
staleness state, and Git provenance when available.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracelayer.evidence.freshness import proof_level
from tracelayer.git.repo import GitRepo
from tracelayer.graph.models import Edge, Node
from tracelayer.graph.store import GraphStore
from tracelayer.knowledge import knowledge_for
from tracelayer.work import normalize_question_state, normalize_task_state

# Upstream intent predicates in render order (spec 28.3).
_UPSTREAM_PREDICATES = ("work", "satisfies", "implements", "addresses", "derived_from")
# Downstream dependents (spec 28.3 "Verification" and §Q context.py).
_DOWNSTREAM_PREDICATES = ("verifies", "exercises", "documents", "deploys")
_PREDICATE_RANK = {p: i for i, p in enumerate(_UPSTREAM_PREDICATES + _DOWNSTREAM_PREDICATES)}
_VERIFY_PREDICATES = ("verifies", "exercises")  # predicates that link tests
_MAX_COMMITS = 50  # bound on the provenance commit list
# Workflow edges shown in the engineering briefing (spec Section 49).
_WORKFLOW_OUT = (
    "blocked_by",
    "blocks",
    "depends_on",
    "asks",
    "answers",
    "answered_by",
    "contains",
    "parent",
    "child",
    "related_to",
    "discovered_from",
)
_WORKFLOW_HEADERS = {
    "blocked_by": "Blocked by",
    "depends_on": "Depends on",
    "asks": "Asks",
    "answers": "Answers",
    "answered_by": "Answered by",
    "contains": "Contains",
    "parent": "Parent",
    "child": "Child",
    "related_to": "Related to",
    "discovered_from": "Discovered from",
}


@dataclass
class VerificationStatus:
    test_trace_id: str
    outcome: str | None  # latest recorded outcome or None
    proof_level: int
    current: bool


# trace:exempt reason=data-container
@dataclass
class ContextResult:
    node: Node
    upstream: list[tuple[Edge, Node]]  # declared edges OUT (intent)
    downstream: list[tuple[Edge, Node]]  # declared edges IN (dependents)
    verification: list[VerificationStatus]
    staleness: str
    provenance: dict  # first_seen/last_modified/commits; may be absent
    related: list[tuple[str, Node]] = field(default_factory=list)
    adjacent: dict = field(default_factory=dict)
    knowledge: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)


def _framework_id_of(test_node: Node) -> str:
    """Framework test id used to look up outcomes; falls back to the trace id.

    Evidence ingest keys outcomes by the framework test id; the test node
    records it in metadata when known.
    """
    value = test_node.metadata.get("framework_test_id")
    return str(value) if value else test_node.trace_id


# trace:v1 id=impl.context.adjacent work=WORK-documentation-artifact-system-and-useful-context-engine satisfies=REQ-engineering-briefing-context
def extract_adjacent(root, node: Node) -> dict:
    """Leading comments + source excerpt for a node (spec Section 44).

    Language-agnostic: consecutive comment lines directly above the symbol
    plus the opening lines of its body, bounded for briefing use.
    """
    from pathlib import Path

    start = node.source_start_line
    if not root or not node.canonical_path or not start:
        return {}
    try:
        lines = (Path(root) / node.canonical_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    prefixes = ("#", "//", "--", "%", ";", "*", "<!--")
    comments: list[str] = []
    idx = start - 2
    while idx >= 0 and len(comments) < 8:
        stripped = lines[idx].strip()
        if not stripped or not stripped.startswith(prefixes):
            break
        comments.append(lines[idx].rstrip())
        idx -= 1
    comments.reverse()
    end = node.source_end_line or start + 11
    excerpt = "\n".join(lines[start - 1 : min(end, start + 11)])
    adjacent: dict = {}
    if comments:
        adjacent["leading_comments"] = comments
    if excerpt.strip():
        adjacent["excerpt"] = excerpt[:1200]
    return adjacent


# trace:v1 id=impl.context.briefing work=WORK-documentation-artifact-system-and-useful-context-engine satisfies=REQ-engineering-briefing-context
def build_context(
    store: GraphStore,
    gitrepo: GitRepo | None,
    trace_id: str,
    root=None,
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

    related: list[tuple[str, Node]] = []
    for edge in store.edges_from(uid):
        if edge.predicate not in _WORKFLOW_OUT:
            continue
        header = _WORKFLOW_HEADERS.get(edge.predicate, edge.predicate.capitalize())
        if edge.predicate == "blocks":
            header = "Blocks"
        target = store.get_node(uid=edge.to_uid)
        if target is not None:
            related.append((header, target))
    for edge in store.edges_to(uid, "blocks"):
        if edge.status != "active":
            continue
        source = store.get_node(uid=edge.from_uid)
        if source is not None:
            related.append(("Blocked by", source))
    related.sort(key=lambda p: (p[0], p[1].trace_id))
    return ContextResult(
        node=node,
        upstream=upstream,
        downstream=downstream,
        verification=verification,
        staleness=node.status(),
        provenance=provenance,
        related=[(header, target) for header, target in related],
        adjacent=extract_adjacent(root, node),
        knowledge=knowledge_for(store, trace_id),
        facts=_context_facts(store, root, node),
    )


# trace:exempt reason=internal-helper
def _context_facts(store: GraphStore, root, node: Node) -> list[dict]:
    """Verify results for this node's facts: itself plus depended-on facts."""
    from tracelayer.facts import DEPENDENT_PREDICATES, FACT_TYPES, verify_facts

    if root is None:
        return []
    wanted: set[str] = set()
    if node.node_type in FACT_TYPES:
        wanted.add(node.trace_id)
    for edge in store.edges_from(node.entity_uid):
        if edge.status != "active" or edge.predicate not in DEPENDENT_PREDICATES:
            continue
        target = store.get_node(uid=edge.to_uid)
        if target is not None and target.node_type in FACT_TYPES:
            wanted.add(target.trace_id)
    if not wanted:
        return []
    try:
        results = verify_facts(store, root)
    except Exception:
        return []
    return [r for r in results if r["id"] in wanted]


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


# trace:exempt reason=internal-helper
def _related_label(target: Node) -> str:
    """Trace id with lifecycle state for task/question nodes."""
    if target.node_type == "task":
        return f"{target.trace_id} [{normalize_task_state(target.metadata.get('state'))}]"
    if target.node_type == "question":
        return f"{target.trace_id} [{normalize_question_state(target.metadata.get('state'))}]"
    return target.trace_id


# trace:v1 id=impl.context.briefing-render work=WORK-documentation-artifact-system-and-useful-context-engine satisfies=REQ-engineering-briefing-context
def render_context_text(ctx: ContextResult) -> str:
    """Render the context summary in the spec 28.3 layout plus briefing sections."""
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

    groups: dict[str, list[str]] = {}
    for header, target in ctx.related:
        item = _related_label(target)
        groups.setdefault(header, [])
        if item not in groups[header]:
            groups[header].append(item)
    for header, items in groups.items():
        lines.append("")
        lines.append(f"{header}:")
        lines.extend(f"  {item}" for item in items)

    if ctx.knowledge:
        lines.append("")
        lines.append("Relevant knowledge:")
        for item in ctx.knowledge:
            lines.append(f"  {item['id']} [{item['type']}, {item['state']}] — {item['title']}")
    for fact in ctx.facts:
        lines.append("")
        lines.append(f"Canonical fact {fact['id']} [{fact['status']}]:")
        if fact.get("canonical") is not None:
            lines.append(f"  canonical: {fact['canonical']}")
        for dep in fact.get("dependents", []):
            if dep["id"] == ctx.node.trace_id or dep["status"] != "CURRENT":
                detail = f"  {dep['id']} {dep['predicate']}: {dep['status']}"
                if dep.get("observed") != dep.get("expected"):
                    detail += (
                        f" (observed {dep.get('observed')!r}, expected {dep.get('expected')!r})"
                    )
                lines.append(detail)

    adjacent = ctx.adjacent or {}
    comments = adjacent.get("leading_comments") or []
    excerpt = adjacent.get("excerpt") or ""
    if comments or excerpt.strip():
        lines.append("")
        lines.append("Nearby context:")
        lines.extend(f"  {comment}" for comment in comments)
        if excerpt.strip():
            lines.append("  ---")
            lines.extend(f"  {line}" for line in excerpt.splitlines())

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
