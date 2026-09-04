"""Tests for the artifact planning engine (spec Sections 19-20)."""

from __future__ import annotations

from tracelayer.planning import (
    classify_scope,
    plan_artifacts,
    plan_steps_for,
    suggest_questions,
)
from tracelayer.tasks import bundle_from_prompt


# trace:v1 id=test.planning.scope-tiers type=test verifies=REQ-artifact-planning-engine
def test_scope_tiers() -> None:
    assert classify_scope("Rename internal variable", "refactor")["scope"] == "tiny"
    assert classify_scope("Add --json output flag", "new_feature")["scope"] == "small"
    assert classify_scope("Add scanner", "new_feature", 3)["scope"] == "medium"
    assert classify_scope("Build a greenfield CLI", "greenfield_project")["scope"] == "medium"
    assert (
        classify_scope("Replace the local index with a distributed index service")["scope"]
        == "large"
    )
    assert classify_scope("Migrate the schema", "maintenance")["scope"] == "large"


# trace:v1 id=test.planning.artifact-shape type=test verifies=REQ-artifact-planning-engine
def test_artifact_plan_shape_and_depth() -> None:
    tiny = plan_artifacts("Rename x to y", kind="refactor")
    assert tiny["scope"] == "tiny"
    assert tiny["requirements"] is False
    assert tiny["spec"] == "none"
    assert tiny["plan"] is False and tiny["tasks"] is False
    assert tiny["suggested_questions"] == []
    small = plan_artifacts("Add --json output flag")
    assert small["scope"] == "small"
    assert small["spec"] == "small"
    assert small["plan"] is True and small["tasks"] is True
    assert small["rfc"] is False
    medium = plan_artifacts("Add repository scanning", num_requirements=2)
    assert medium["spec"] == "full"
    assert medium["adr"] == "possible"
    large = plan_artifacts("Replace the local index with a distributed index service")
    assert large["scope"] == "large"
    assert large["spec"] == "full"
    assert large["adr"] is True
    assert set(plan_artifacts("x")) == {
        "scope",
        "scope_signals",
        "work",
        "requirements",
        "spec",
        "rfc",
        "adr",
        "plan",
        "tasks",
        "runbook",
        "docs_update",
        "suggested_questions",
    }


# trace:v1 id=test.planning.questions type=test verifies=REQ-question-detection
def test_suggest_questions_only_interrogatives() -> None:
    assert suggest_questions("Should symlinked dirs count once? Do it.") == [
        "Should symlinked dirs count once?"
    ]
    assert suggest_questions("Rename the variable") == []
    assert len(suggest_questions("A? B? C? D? E? F?")) == 5


# trace:v1 id=test.planning.bundle-steps type=test verifies=REQ-real-bootstrap-plan-generation
def test_bundle_plan_has_verification_tail() -> None:
    bundle = bundle_from_prompt("Add --json output flag")
    steps = bundle["plan"]["steps"]
    assert len(steps) == 3
    assert steps[0].startswith("Implement: ")
    assert steps[1] == "Add or extend verification tests"
    assert steps[2] == "Run verification and finalize"
    assert plan_steps_for([], "tiny") == ["Implement the requested change"]
