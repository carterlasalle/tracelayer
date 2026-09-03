"""Graph ontology registry (spec Section 12, FR-003, FR-004).

Built-in edge semantics MUST remain stable within a major protocol version.
The registry is the single source of truth for generated docs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeTypeDef:
    name: str
    category: str
    description: str


@dataclass(frozen=True)
class EdgeTypeDef:
    name: str
    kind: str  # semantic | structural | observed
    description: str
    typical: str


_NODE_TYPES: list[tuple[str, str, str]] = [
    # (name, category, description)
    ("goal", "intent", "Top-level business or product goal."),
    ("prd", "intent", "Product requirements document."),
    ("requirement", "intent", "A formal, stable, testable requirement."),
    ("nfr", "intent", "A non-functional requirement."),
    (
        "decision",
        "decision/planning",
        "An architecture decision record (ADR) or equivalent decision.",
    ),
    ("work", "decision/planning", "A work item (issue, ticket, task) that produced artifacts."),
    ("task", "decision/planning", "A durable engineering task with lifecycle state."),
    ("question", "decision/planning", "A material open question blocking work."),
    ("spec", "intent", "A specification document."),
    ("rfc", "decision/planning", "A request-for-comments design proposal."),
    ("plan", "decision/planning", "A plan or plan step; first-class ID (PLAN-X/P3)."),
    ("plan_step", "decision/planning", "A single step within a plan."),
    ("implementation", "realization", "Source code realizing a requirement/decision."),
    ("config", "realization", "Configuration with contractual significance."),
    ("operation", "realization", "Deployment, runbook-adjacent operational behavior."),
    ("data", "realization", "Data schema or dataset artifact."),
    ("prompt", "realization", "Prompt or configuration encoding a product invariant."),
    ("test", "verification/documentation", "A verification test."),
    ("document", "verification/documentation", "Human-facing documentation artifact."),
    ("runbook", "verification/documentation", "Operational runbook procedure."),
    ("evidence", "verification/documentation", "Immutable runtime/CI evidence record."),
    ("commit", "provenance", "A Git commit."),
    ("pull_request", "provenance", "A pull/merge request."),
    ("ci_run", "provenance", "A CI run."),
    ("external", "provenance", "An external system record (Jira, Linear, Notion, ...)."),
]

_SEMANTIC: list[tuple[str, str, str]] = [
    (
        "work",
        "source was produced or authorized by the target work item",
        "implementation/test/document -> WORK-...",
    ),
    ("derived_from", "source was derived from target", "requirement -> PRD; plan -> decision"),
    ("addresses", "source is intended to address target", "decision/work -> requirement"),
    ("satisfies", "source behavior fulfills target contract", "implementation -> requirement"),
    (
        "implements",
        "source realizes target plan or decision",
        "implementation/config -> plan/decision",
    ),
    ("verifies", "source test or evidence verifies target contract", "test -> requirement"),
    ("exercises", "source test intends to execute target implementation", "test -> implementation"),
    ("documents", "source documents target", "document/runbook -> implementation/requirement"),
    ("deploys", "source operation or config deploys target", "operation -> implementation"),
    (
        "depends_on",
        "semantic dependency beyond trivial inferred calls",
        "implementation/requirement -> artifact",
    ),
    ("supersedes", "source replaces target", "decision/requirement -> same type"),
    ("produces", "source activity produces target", "plan/CI -> implementation/evidence"),
    ("consumes", "source relies on target artifact or data", "implementation -> data/config"),
    ("blocks", "source must resolve before target progresses", "work/requirement -> work/release"),
    ("blocked_by", "source is blocked by target", "task -> task/question"),
    ("related_to", "source is related to target", "artifact -> artifact"),
    ("discovered_from", "source was discovered while working on target", "task/work -> task/work"),
    ("asks", "source poses target question", "task/work -> question"),
    ("answers", "source answers target question", "decision -> question"),
    ("answered_by", "source question is answered by target", "question -> decision"),
    ("resolves", "source resolves target", "decision/implementation -> question/task"),
    ("proposes", "source proposes target design", "rfc -> spec/decision"),
    ("decides", "source records the decision for target", "decision -> question/requirement"),
    ("parent", "source is the parent of target in a work hierarchy", "work/task -> task"),
    ("child", "source is a child of target in a work hierarchy", "task -> work/task"),
    ("introduced_by", "source was introduced by target activity", "artifact -> work/commit"),
]

_STRUCTURAL: list[tuple[str, str, str]] = [
    ("contains", "structural containment", "parent symbol -> child symbol"),
    ("calls", "function call relationship", "caller -> callee"),
    ("imports", "import or use of another module", "module -> module"),
    ("inherits", "class inheritance", "subclass -> base class"),
    ("references_symbol", "identifier reference", "reference site -> symbol"),
    ("reads", "reads a field or variable", "reader -> field"),
    ("writes", "writes a field or variable", "writer -> field"),
    ("changed_by", "revision that changed the artifact", "node -> commit"),
    ("owned_by", "ownership relationship", "node -> owner"),
]

_OBSERVED: list[tuple[str, str, str]] = [
    ("executed", "test executed the implementation at runtime", "test -> implementation"),
    ("passed", "test passed in an evidence run", "test -> run"),
    ("failed", "test failed in an evidence run", "test -> run"),
    ("built_in", "artifact built in an evidence run", "artifact -> run"),
    ("deployed_in", "artifact deployed in an environment", "artifact -> environment"),
    ("attested_by", "evidence attestation", "artifact -> attestation"),
]


# trace:v1 id=impl.protocol.work-model-ontology work=WORK-trace-layer-native-work-task-question-decision-model satisfies=REQ-native-work-task-question-decision-ontology
def _build_nodes() -> dict[str, NodeTypeDef]:
    return {name: NodeTypeDef(name, cat, desc) for name, cat, desc in _NODE_TYPES}


def _build_edges(kind: str, rows: list[tuple[str, str, str]]) -> dict[str, EdgeTypeDef]:
    return {name: EdgeTypeDef(name, kind, desc, typical) for name, desc, typical in rows}


NODE_TYPES: dict[str, NodeTypeDef] = _build_nodes()

EDGE_TYPES: dict[str, EdgeTypeDef] = {
    **_build_edges("semantic", _SEMANTIC),
    **_build_edges("structural", _STRUCTURAL),
    **_build_edges("observed", _OBSERVED),
}

SEMANTIC_EDGES: frozenset[str] = frozenset(name for name, _, _ in _SEMANTIC)
STRUCTURAL_EDGES: frozenset[str] = frozenset(name for name, _, _ in _STRUCTURAL)
OBSERVED_EDGES: frozenset[str] = frozenset(name for name, _, _ in _OBSERVED)

# Stable ordering used by rendering and generated docs.
EDGE_ORDER: list[str] = [name for name, _, _ in _SEMANTIC + _STRUCTURAL + _OBSERVED]

NODE_CATEGORIES: dict[str, list[str]] = {}
for _n, _c, _d in _NODE_TYPES:
    NODE_CATEGORIES.setdefault(_c, []).append(_n)
