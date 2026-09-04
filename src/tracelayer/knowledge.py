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

    Direct governance edges first (applies_to, warns_against, recommended_for,
    explains); ACTIVE before other lifecycle states; capped so hook context
    stays small. Superseded/invalidated knowledge is excluded by default.
    """
    target = store.get_node(trace_id=artifact_id)
    if target is None:
        return []
    hits: list[tuple[int, str, Node]] = []
    for edge in store.edges_to(target.entity_uid):
        if edge.status != "active" or edge.predicate not in _INJECTION_PREDICATES:
            continue
        node = store.get_node(uid=edge.from_uid)
        if node is None or not node.active or node.node_type not in KNOWLEDGE_TYPES:
            continue
        state = normalize_knowledge_state(node.metadata.get("state"))
        if state in ("SUPERSEDED", "INVALIDATED", "ARCHIVED"):
            continue
        rank = _INJECTION_PREDICATES.index(edge.predicate)
        hits.append(
            (rank if state == "ACTIVE" else rank + len(_INJECTION_PREDICATES), node.trace_id, node)
        )
    hits.sort(key=lambda h: (h[0], h[1]))
    return [
        {
            "id": node.trace_id,
            "type": node.node_type,
            "state": normalize_knowledge_state(node.metadata.get("state")),
            "title": node.title or node.trace_id,
        }
        for _, _, node in hits[:limit]
    ]
