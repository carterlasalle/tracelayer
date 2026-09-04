"""Unit tests for the graph ontology registry (spec Section 12, FR-003/FR-004).

Covers registry completeness (node types, semantic/structural/observed edges),
kind classification sets, and EDGE_ORDER stability.
"""

from __future__ import annotations

from tracelayer.protocol import (
    EDGE_ORDER,
    EDGE_TYPES,
    NODE_CATEGORIES,
    NODE_TYPES,
    OBSERVED_EDGES,
    SEMANTIC_EDGES,
    STRUCTURAL_EDGES,
)
from tracelayer.protocol.ontology import EdgeTypeDef, NodeTypeDef

# Spec Section 12.1 — five categories, twenty-five node classes (vNext work model).
SPEC_NODE_TYPES = [
    # intent
    "goal",
    "prd",
    "requirement",
    "nfr",
    "spec",
    # decision/planning
    "decision",
    "work",
    "task",
    "question",
    "rfc",
    "plan",
    "plan_step",
    # realization
    "implementation",
    "config",
    "operation",
    "data",
    "prompt",
    # verification/documentation
    "test",
    "document",
    "runbook",
    "evidence",
    # provenance
    "commit",
    "pull_request",
    "ci_run",
    "external",
    # knowledge (addendum Sections 82, 122)
    "finding",
    "learning",
    "anti_pattern",
    "convention",
    "constraint",
    "fact",
    "value",
]

SPEC_NODE_CATEGORIES = {
    "intent": ["goal", "prd", "requirement", "nfr", "spec"],
    "decision/planning": ["decision", "work", "task", "question", "rfc", "plan", "plan_step"],
    "realization": ["implementation", "config", "operation", "data", "prompt"],
    "verification/documentation": ["test", "document", "runbook", "evidence"],
    "provenance": ["commit", "pull_request", "ci_run", "external"],
    "knowledge": [
        "finding",
        "learning",
        "anti_pattern",
        "convention",
        "constraint",
        "fact",
        "value",
    ],
}

# Spec 12.2 (13 edges) plus the `work` convenience edge (spec 11.3/33.1),
# plus the vNext work-relationship edges (spec Sections 6, 77).
SPEC_SEMANTIC = [
    "work",
    "derived_from",
    "addresses",
    "satisfies",
    "implements",
    "verifies",
    "exercises",
    "documents",
    "deploys",
    "depends_on",
    "supersedes",
    "produces",
    "consumes",
    "blocks",
    "blocked_by",
    "related_to",
    "discovered_from",
    "asks",
    "answers",
    "answered_by",
    "resolves",
    "proposes",
    "decides",
    "parent",
    "child",
    "introduced_by",
    "applies_to",
    "learned_from",
    "establishes",
    "canonicalizes",
    "depends_on_value",
    "documents_value",
    "mirrors_value",
    "derives_value",
    "generated_from",
    "historical_reference",
    "explains",
    "warns_against",
    "recommended_for",
]

# Spec 12.3.
SPEC_STRUCTURAL = [
    "contains",
    "calls",
    "imports",
    "inherits",
    "references_symbol",
    "reads",
    "writes",
    "changed_by",
    "owned_by",
]

# Spec 12.4.
SPEC_OBSERVED = ["executed", "passed", "failed", "built_in", "deployed_in", "attested_by"]


# trace:v1 id=test.dogfood.tests.unit.protocol.test_ontology.py type=test
def test_node_type_registry_matches_spec() -> None:
    assert sorted(NODE_TYPES) == sorted(SPEC_NODE_TYPES)
    assert len(NODE_TYPES) == 32


def test_node_type_defs_well_formed() -> None:
    for name, t in NODE_TYPES.items():
        assert isinstance(t, NodeTypeDef)
        assert t.name == name
        assert t.category
        assert t.description


def test_node_categories_match_spec() -> None:
    assert NODE_CATEGORIES == SPEC_NODE_CATEGORIES
    assert sum(len(v) for v in NODE_CATEGORIES.values()) == len(NODE_TYPES)


def test_edge_counts() -> None:
    assert len(SEMANTIC_EDGES) == 39
    assert len(STRUCTURAL_EDGES) == 9
    assert len(OBSERVED_EDGES) == 6
    assert len(EDGE_TYPES) == 54


def test_semantic_edges_match_spec() -> None:
    assert set(SEMANTIC_EDGES) == set(SPEC_SEMANTIC)


def test_structural_edges_match_spec() -> None:
    assert set(STRUCTURAL_EDGES) == set(SPEC_STRUCTURAL)


def test_observed_edges_match_spec() -> None:
    assert set(OBSERVED_EDGES) == set(SPEC_OBSERVED)


def test_kind_sets_disjoint_and_exhaustive() -> None:
    assert SEMANTIC_EDGES.isdisjoint(STRUCTURAL_EDGES)
    assert SEMANTIC_EDGES.isdisjoint(OBSERVED_EDGES)
    assert STRUCTURAL_EDGES.isdisjoint(OBSERVED_EDGES)
    assert SEMANTIC_EDGES | STRUCTURAL_EDGES | OBSERVED_EDGES == set(EDGE_TYPES)


def test_edge_defs_well_formed() -> None:
    for name, e in EDGE_TYPES.items():
        assert isinstance(e, EdgeTypeDef)
        assert e.name == name
        assert e.kind in ("semantic", "structural", "observed")
        assert e.description
        assert e.typical


def test_edge_kind_field_consistent() -> None:
    for name in SEMANTIC_EDGES:
        assert EDGE_TYPES[name].kind == "semantic"
    for name in STRUCTURAL_EDGES:
        assert EDGE_TYPES[name].kind == "structural"
    for name in OBSERVED_EDGES:
        assert EDGE_TYPES[name].kind == "observed"


def test_edge_order_stable_and_exact() -> None:
    assert EDGE_ORDER == SPEC_SEMANTIC + SPEC_STRUCTURAL + SPEC_OBSERVED
    assert len(EDGE_ORDER) == len(set(EDGE_ORDER))
    assert set(EDGE_ORDER) == set(EDGE_TYPES)
