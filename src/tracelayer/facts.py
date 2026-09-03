"""Canonical facts and values: one source, tracked dependents (spec Sections 94-112).

A FACT/VALUE node records ``canonical_source`` (``path::dotted.key`` into a
TOML or JSON file) and its last-verified ``value``. ``verify_facts`` compares
the live canonical source plus each dependent's recorded value, reporting
CURRENT or REVIEW_REQUIRED per dependent. Nothing is auto-rewritten.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from tracelayer.graph.store import GraphStore

FACT_TYPES = ("fact", "value")

# Dependent predicates from the dependent's perspective (spec Sections 98, 121).
_DEPENDENT_PREDICATES = ("depends_on_value", "documents_value", "mirrors_value",
                         "derives_value", "generated_from", "historical_reference")


# trace:exempt reason=internal-helper
def read_canonical(root: Path | str, source: str) -> tuple[bool, str]:
    """Read ``path::dotted.key`` from a TOML/JSON file; (found, value)."""
    path_part, sep, key_part = str(source or "").partition("::")
    if not sep or not key_part:
        return False, ""
    path = Path(root) / path_part
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, ""
    try:
        data = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return False, ""
    node: object = data
    for part in key_part.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, ""
        node = node[part]
    if isinstance(node, bool):
        return True, "true" if node else "false"
    if isinstance(node, (str, int, float)):
        return True, str(node)
    return True, json.dumps(node, sort_keys=True)


# trace:v1 id=impl.facts.verify work=WORK-durable-knowledge-nodes-and-canonical-facts satisfies=REQ-canonical-fact-tracking
def verify_facts(store: GraphStore, root: Path | str) -> list[dict]:
    """Drift check for every active FACT/VALUE node with a canonical source."""
    results = []
    for node in sorted(store.all_nodes(active_only=True), key=lambda n: n.trace_id):
        if node.node_type not in FACT_TYPES:
            continue
        source = node.metadata.get("canonical_source")
        if not source:
            continue
        found, current = read_canonical(root, str(source))
        recorded = node.metadata.get("value")
        recorded_text = str(recorded) if recorded is not None else None
        if not found:
            status = "REVIEW_REQUIRED"
        elif recorded_text is None or recorded_text == current:
            status = "CURRENT"
        else:
            status = "REVIEW_REQUIRED"
        dependents = []
        for edge in store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate not in _DEPENDENT_PREDICATES:
                continue
            consumer = store.get_node(uid=edge.from_uid)
            if consumer is None or not consumer.active:
                continue
            if edge.predicate == "historical_reference":
                dep_status = "CURRENT"
            else:
                expected = consumer.metadata.get("value")
                dep_status = (
                    "CURRENT"
                    if expected is None or str(expected) == current
                    else "REVIEW_REQUIRED"
                )
            dependents.append(
                {"id": consumer.trace_id, "predicate": edge.predicate, "status": dep_status}
            )
        dependents.sort(key=lambda d: d["id"])
        results.append(
            {
                "id": node.trace_id,
                "type": node.node_type,
                "canonical_source": str(source),
                "canonical": current if found else None,
                "recorded": recorded_text,
                "status": status,
                "dependents": dependents,
            }
        )
    return results
