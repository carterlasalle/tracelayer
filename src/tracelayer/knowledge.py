"""Durable engineering knowledge: findings, learnings, anti-patterns (spec Sections 81-93).

Knowledge nodes carry lifecycle via ``state=`` and link to the artifacts
they govern via ``applies_to`` (plus warns_against/recommended_for/explains).
``knowledge_for`` resurfaces the most relevant few for a hook briefing;
agents query deeper with ``trace knowledge``.
"""

from __future__ import annotations

from tracelayer.graph.models import Node
from tracelayer.graph.store import GraphStore

KNOWLEDGE_TYPES = ("finding", "learning", "anti_pattern", "convention", "constraint")

KNOWLEDGE_LIFECYCLE = ("ACTIVE", "UNDER_REVIEW", "SUPERSEDED", "INVALIDATED", "ARCHIVED")

# Predicates that govern an artifact, in injection priority order (spec Section 91).
_INJECTION_PREDICATES = ("applies_to", "warns_against", "recommended_for", "explains")


# trace:exempt reason=internal-helper
def normalize_knowledge_state(value: object, default: str = "ACTIVE") -> str:
    """Canonical knowledge lifecycle state (spec Section 86)."""
    canon = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return canon if canon in KNOWLEDGE_LIFECYCLE else default


# trace:v1 id=impl.knowledge.query work=WORK-durable-knowledge-nodes-and-canonical-facts satisfies=REQ-knowledge-node-ontology
def knowledge_for(store: GraphStore, artifact_id: str, limit: int = 3) -> list[dict]:
    """Most relevant active knowledge governing ``artifact_id`` (spec Section 91).

    Ranked traversal: direct governance edges, then knowledge governing the
    artifact's work items, then its requirements, then path/symbol scope
    matches. ACTIVE before other lifecycle states; capped so hook context
    stays small. Superseded/invalidated knowledge is excluded by default.
    """
    target = store.get_node(trace_id=artifact_id)
    if target is None:
        return []
    work_uids = {
        e.to_uid for e in store.edges_from(target.entity_uid, "work") if e.status == "active"
    }
    req_uids = {
        e.to_uid for e in store.edges_from(target.entity_uid, "satisfies") if e.status == "active"
    }
    hits: dict[str, tuple[int, Node, str]] = {}

    # trace:exempt reason=internal-helper
    def consider(node: Node, rank: int, via: str) -> None:
        if node.node_type not in KNOWLEDGE_TYPES:
            return
        state = normalize_knowledge_state(node.metadata.get("state"))
        if state in ("SUPERSEDED", "INVALIDATED", "ARCHIVED"):
            return
        key = (rank if state == "ACTIVE" else rank + 10, node.trace_id)
        if node.trace_id not in hits or key < (hits[node.trace_id][0], node.trace_id):
            hits[node.trace_id] = (key[0], node, via)

    for edge in store.edges_to(target.entity_uid):
        if edge.status != "active" or edge.predicate not in _INJECTION_PREDICATES:
            continue
        node = store.get_node(uid=edge.from_uid)
        if node is not None and node.active:
            consider(node, _INJECTION_PREDICATES.index(edge.predicate), "direct")
    for uid in work_uids | req_uids:
        via = "work" if uid in work_uids else "requirement"
        rank = 10 if via == "work" else 11
        for edge in store.edges_to(uid):
            if edge.status != "active" or edge.predicate not in _INJECTION_PREDICATES:
                continue
            node = store.get_node(uid=edge.from_uid)
            if node is not None and node.active:
                consider(node, rank, via)
    scope_haystacks = [
        (target.canonical_path or "").lower(),
        (target.symbol_qualified_name or "").lower(),
    ]
    for node in store.all_nodes(active_only=True):
        if node.node_type not in KNOWLEDGE_TYPES:
            continue
        scope = str(node.metadata.get("scope") or "").strip().lower()
        if not scope:
            continue
        if any(scope and scope in hay for hay in scope_haystacks if hay):
            consider(node, 12, "scope")
    ranked = sorted(hits.values(), key=lambda h: (h[0], h[1].trace_id))
    return [
        {
            "id": node.trace_id,
            "type": node.node_type,
            "state": normalize_knowledge_state(node.metadata.get("state")),
            "title": node.title or node.trace_id,
            "via": via,
        }
        for _, node, via in ranked[:limit]
    ]
