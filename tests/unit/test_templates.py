"""Tests for the artifact template registry (spec Sections 67-68)."""

from __future__ import annotations

from tracelayer.templates import get_template, template_types, validate_structure


# trace:v1 id=test.templates.registry type=test verifies=REQ-artifact-template-registry
def test_registry_covers_spec_types() -> None:
    types = template_types()
    for expected in (
        "work",
        "task",
        "question",
        "decision",
        "requirement",
        "spec",
        "rfc",
        "adr",
        "plan",
        "runbook",
        "guide",
        "reference",
        "migration",
        "incident",
    ):
        assert expected in types
        template = get_template(expected)
        assert template is not None
        assert template["sections"]
        assert template["required"]
        assert set(template["required"]) <= set(template["sections"])
        assert template["states"]
        assert template["relationships"]
    assert get_template("bogus") is None
    assert get_template("ADR") is not None


# trace:v1 id=test.templates.validation type=test verifies=REQ-artifact-template-registry
def test_validate_structure_reports_missing_required() -> None:
    assert validate_structure("adr", ["Status", "Context", "Decision"]) == []
    assert validate_structure("adr", ["Context"]) == ["status", "decision"]
    assert validate_structure("spec", ["Summary"]) == ["requirements"]
    assert validate_structure("bogus", []) == []
    assert validate_structure("runbook", ["PROCEDURE"]) == []
