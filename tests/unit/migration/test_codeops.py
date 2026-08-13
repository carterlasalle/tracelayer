"""Tests for the CodeOps migration pipeline (spec 33, contract §M).

Covers ``scan_codeops`` / ``build_plan`` / ``apply_plan``: deterministic
classification of every known CodeOps variant, no silent field loss,
deterministic ID generation, and apply-time behavior (rewrite only
deterministic + high_confidence items; dry_run touches nothing).
"""

from __future__ import annotations

from pathlib import Path

from tracelayer.config import Project, TraceConfig
from tracelayer.diagnostics import SEVERITY_INFO
from tracelayer.migration.codeops import apply_plan, build_plan, scan_codeops


def make_project(root: Path, **cfg) -> Project:
    return Project(root=root, config=TraceConfig(repo_id="probe", **cfg))


def scan(root: Path, **cfg):
    return scan_codeops(root, make_project(root, **cfg).config)


def plan_for(root: Path, rel: str, line: str, **cfg):
    """Scan a single-file repo and return (item, scan_diags) for ``rel``."""
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(line + "\n")
    markers, diags = scan(root, **cfg)
    project = make_project(root, **cfg)
    plan = build_plan(markers, project)
    assert [m.path for m in markers] == [rel]
    return plan.items[0], diags


# ---------------------------------------------------------------------------
# scan_codeops
# ---------------------------------------------------------------------------


def test_scan_finds_markers_in_multiple_styles_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("# codeops:trace work_item=B-1\n")
    (tmp_path / "a.txt").write_text("<!-- codeops:trace work_item=A-1 -->\n")
    (tmp_path / "c.py").write_text("// codeops:trace work_item=C-1\n")
    markers, diags = scan(tmp_path)
    assert [(m.path, m.line, m.fields) for m in markers] == [
        ("a.txt", 1, {"work_item": "A-1"}),
        ("c.py", 1, {"work_item": "C-1"}),
        ("src/b.py", 1, {"work_item": "B-1"}),
    ]
    assert diags == []


def test_scan_records_blank_values(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("# codeops:trace work_item= plan=PLAN-1\n")
    markers, _ = scan(tmp_path)
    assert markers[0].fields == {"work_item": "", "plan": "PLAN-1"}


def test_scan_keeps_first_duplicate_value_with_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "# codeops:trace work_item=A-1 work_item=B-2 badtoken\n"
    )
    markers, diags = scan(tmp_path)
    assert markers[0].fields == {"work_item": "A-1"}
    msgs = [d.message for d in diags]
    assert any("Duplicate codeops field 'work_item'" in m for m in msgs)
    assert any("Malformed codeops field token 'badtoken' ignored" in m for m in msgs)


def test_scan_unknown_and_undocumented_fields_kept_with_info_diagnostics(
    tmp_path: Path,
) -> None:
    (tmp_path / "m.py").write_text(
        "# codeops:trace work_item=A-1 extra_field=zzz ops=deploy.py incident=INC-9\n"
    )
    markers, diags = scan(tmp_path)
    assert markers[0].fields == {
        "work_item": "A-1",
        "extra_field": "zzz",
        "ops": "deploy.py",
        "incident": "INC-9",
    }
    infos = [(d.rule_id, d.severity, d.message) for d in diags]
    assert all(d[1] == SEVERITY_INFO for d in infos)
    assert any("Unknown codeops field extra_field=zzz preserved for review" in d[2] for d in infos)
    assert any("Undocumented codeops field ops=deploy.py" in d[2] for d in infos)
    assert any("Undocumented codeops field incident=INC-9" in d[2] for d in infos)


def test_scan_skips_binary_excluded_and_generated_files(tmp_path: Path) -> None:
    (tmp_path / "ignored").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "generated").mkdir(parents=True)
    (tmp_path / "keep.py").write_text("# codeops:trace work_item=K-1\n")
    (tmp_path / "ignored" / "x.py").write_text("# codeops:trace work_item=X-1\n")
    (tmp_path / "src" / "generated" / "y.py").write_text("# codeops:trace work_item=Y-1\n")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01" + b"codeops:trace work_item=B-1")
    markers, _ = scan(
        tmp_path, discovery={"exclude": ["ignored/**"], "generated": ["src/generated/**"]}
    )
    assert [m.path for m in markers] == ["keep.py"]


# ---------------------------------------------------------------------------
# build_plan: classification + rendering
# ---------------------------------------------------------------------------


def test_work_item_maps_to_work_deterministic(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "src/billing.py", "# codeops:trace work_item=ABC-123")
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123"
    )
    assert "work_item=ABC-123 -> work=ABC-123" in item.note


def test_work_item_in_test_file_uses_test_node(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "tests/test_billing.py", "# codeops:trace work_item=ABC-123")
    assert item.classification == "deterministic"
    assert item.new_marker == "# trace:v1 id=test.abc-123 type=test work=ABC-123"


def test_spec_on_test_file_verifies_high_confidence(tmp_path: Path) -> None:
    item, _ = plan_for(
        tmp_path,
        "tests/test_billing.py",
        "# codeops:trace spec=specs/billing.md#REQ-17",
    )
    assert item.classification == "high_confidence"
    assert item.new_marker == (
        "# trace:v1 id=test.req-17 type=test verifies=REQ-17"
    )
    assert "-> verifies=REQ-17 (test attachment)" in item.note


def test_spec_on_source_file_satisfies_high_confidence(tmp_path: Path) -> None:
    item, _ = plan_for(
        tmp_path, "src/billing.py", "# codeops:trace spec=specs/billing.md#REQ-17"
    )
    assert item.classification == "high_confidence"
    assert item.new_marker == (
        "# trace:v1 id=impl.req-17 type=implementation satisfies=REQ-17"
    )
    assert "-> satisfies=REQ-17 (implementation attachment)" in item.note


def test_spec_bare_id_on_source_satisfies(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "src/billing.py", "# codeops:trace spec=REQ-17")
    assert item.classification == "high_confidence"
    assert item.new_marker == (
        "# trace:v1 id=impl.req-17 type=implementation satisfies=REQ-17"
    )


def test_unresolvable_spec_requires_review_with_tl002(tmp_path: Path) -> None:
    item, _ = plan_for(
        tmp_path, "src/billing.py", "# codeops:trace spec=specs/billing.md"
    )
    assert item.classification == "requires_review"
    assert item.new_marker is None
    assert "cannot be resolved to a requirement ID" in item.note
    assert [(d.rule_id, d.line) for d in item.diagnostics] == [("TL002", 1)]


def test_unresolvable_spec_with_work_item_keeps_deterministic_plus_tl002(
    tmp_path: Path,
) -> None:
    item, _ = plan_for(
        tmp_path, "src/billing.py", "# codeops:trace spec=specs/billing.md work_item=ABC-123"
    )
    # The real edge (work) wins; the unresolvable spec is preserved, not lost.
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123"
    )
    assert "spec=specs/billing.md cannot be resolved" in item.note
    assert [d.rule_id for d in item.diagnostics] == ["TL002"]


def test_plan_maps_to_implements_deterministic(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "src/billing.py", "# codeops:trace plan=PLAN-2")
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "# trace:v1 id=impl.plan-2 type=implementation implements=PLAN-2"
    )


def test_commit_derived_and_dropped(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "src/billing.py", "# codeops:trace commit=abc123")
    assert item.classification == "derived"
    assert item.new_marker is None
    assert "dropped from source; recorded as import metadata" in item.note


def test_note_only_fields_never_demote_marker(tmp_path: Path) -> None:
    item, _ = plan_for(
        tmp_path,
        "src/billing.py",
        "# codeops:trace work_item=ABC-123 commit=abc123 "
        "test=tests/test_x.py prompt=helper.md ops=deploy.py incident=INC-9",
    )
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123"
    )
    for key, value in {
        "commit": "abc123",
        "test": "tests/test_x.py",
        "prompt": "helper.md",
        "ops": "deploy.py",
        "incident": "INC-9",
    }.items():
        assert f"{key}={value}" in item.note


def test_ref_fields_consolidated_onto_work_note(tmp_path: Path) -> None:
    item, _ = plan_for(
        tmp_path,
        "src/billing.py",
        "# codeops:trace work_item=ABC-123 jira_ref=JIRA-9 github_ref=gh-1 notion_ref=nb-2",
    )
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123"
    )
    for key, value in {"jira_ref": "JIRA-9", "github_ref": "gh-1", "notion_ref": "nb-2"}.items():
        assert f"{key}={value} consolidated onto the work node" in item.note


def test_unknown_field_preserved_no_silent_loss(tmp_path: Path) -> None:
    item, diags = plan_for(
        tmp_path, "src/billing.py", "# codeops:trace work_item=ABC-123 extra_field=zzz"
    )
    assert item.classification == "deterministic"
    assert "unknown field extra_field=zzz preserved for review" in item.note
    assert any("Unknown codeops field extra_field=zzz" in d.message for d in diags)


def test_blank_placeholder_dropped(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "src/billing.py", "# codeops:trace work_item=")
    assert item.classification == "dropped"
    assert item.new_marker is None
    assert "work_item= blank placeholder omitted" in item.note


def test_inline_html_marker_preserves_wrapping(tmp_path: Path) -> None:
    item, _ = plan_for(tmp_path, "notes.md", "<!-- codeops:trace work_item=WF-1 -->")
    assert item.classification == "deterministic"
    assert item.new_marker == (
        "<!-- trace:v1 id=doc.wf-1 type=document work=WF-1 -->"
    )


def test_duplicate_work_item_ids_are_unique_and_deterministic(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# codeops:trace work_item=ABC-123\n")
    (tmp_path / "src" / "b.py").write_text("# codeops:trace work_item=ABC-123\n")
    project = make_project(tmp_path)
    plan = build_plan(scan_codeops(tmp_path, project.config)[0], project)
    ids = [it.new_marker for it in plan.items]
    assert ids == [
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123",
        "# trace:v1 id=impl.abc-123-2 type=implementation work=ABC-123",
    ]
    assert len({it.new_marker for it in plan.items}) == 2


def test_build_plan_is_deterministic_across_runs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "# codeops:trace work_item=ABC-123 spec=specs/billing.md#REQ-17 plan=PLAN-2\n"
    )
    project = make_project(tmp_path)
    markers, _ = scan_codeops(tmp_path, project.config)
    first = build_plan(markers, project)
    second = build_plan(markers, project)
    assert first.schema == "tracelayer-migration/v1"
    assert [it.new_marker for it in first.items] == [
        it.new_marker for it in second.items
    ]
    assert first.summary == second.summary


def test_plan_summary_counts_per_classification(tmp_path: Path) -> None:
    lines = [
        "# codeops:trace work_item=A-1",            # deterministic
        "# codeops:trace spec=REQ-1",               # high_confidence (source)
        "# codeops:trace prompt=p.md",              # requires_review
        "# codeops:trace commit=abc",               # derived
        "# codeops:trace work_item=",               # dropped
    ]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("\n".join(lines) + "\n")
    project = make_project(tmp_path)
    plan = build_plan(scan_codeops(tmp_path, project.config)[0], project)
    assert plan.summary == {
        "dropped": 1,
        "deterministic": 1,
        "high_confidence": 1,
        "derived": 1,
        "requires_review": 1,
    }


# ---------------------------------------------------------------------------
# apply_plan
# ---------------------------------------------------------------------------


def test_apply_plan_dry_run_changes_nothing_on_disk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "billing.py"
    f.write_text("# codeops:trace work_item=ABC-123 commit=abc123\n")
    project = make_project(tmp_path)
    markers, _ = scan_codeops(tmp_path, project.config)
    plan = build_plan(markers, project)
    result = apply_plan(plan, tmp_path, project.config, dry_run=True)
    assert result["dry_run"] is True
    assert result["applied"] == 1
    assert result["files"]["src/billing.py"]["rewritten"] == 1
    assert f.read_text() == "# codeops:trace work_item=ABC-123 commit=abc123\n"


def test_apply_plan_rewrites_only_deterministic_and_high_confidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "billing.py"
    f.write_text(
        "# codeops:trace work_item=ABC-123 commit=abc123\n"
        "def bill():\n"
        "    pass\n"
        "# codeops:trace spec=specs/billing.md#REQ-17 plan=PLAN-2\n"
        "# codeops:trace prompt=helper.md\n"
        "# codeops:trace commit=abc123\n"
    )
    project = make_project(tmp_path)
    plan = build_plan(scan_codeops(tmp_path, project.config)[0], project)
    result = apply_plan(plan, tmp_path, project.config)
    assert result["dry_run"] is False
    assert result["applied"] == 2
    assert result["changed_files"] == 1
    assert result["files"]["src/billing.py"]["rewritten"] == 2
    assert result["files"]["src/billing.py"]["requires_review"] == 1
    assert result["files"]["src/billing.py"]["derived"] == 1
    assert f.read_text() == (
        "# trace:v1 id=impl.abc-123 type=implementation work=ABC-123\n"
        "def bill():\n"
        "    pass\n"
        "# trace:v1 id=impl.req-17 type=implementation satisfies=REQ-17 implements=PLAN-2\n"
        "# codeops:trace prompt=helper.md\n"
        "# codeops:trace commit=abc123\n"
    )


def test_apply_plan_preserves_inline_html_wrapping(tmp_path: Path) -> None:
    f = tmp_path / "notes.md"
    f.write_text("<!-- codeops:trace work_item=WF-1 -->\n\ncontext\n")
    project = make_project(tmp_path)
    plan = build_plan(scan_codeops(tmp_path, project.config)[0], project)
    apply_plan(plan, tmp_path, project.config)
    assert f.read_text() == (
        "<!-- trace:v1 id=doc.wf-1 type=document work=WF-1 -->\n\ncontext\n"
    )


def test_apply_plan_missing_file_degrades_gracefully(tmp_path: Path) -> None:
    # A plan item whose file vanished between plan and apply is skipped, and
    # the report still counts the other file.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# codeops:trace work_item=A-1\n")
    project = make_project(tmp_path)
    plan = build_plan(scan_codeops(tmp_path, project.config)[0], project)
    assert len(plan.items) == 1
    (tmp_path / "src" / "a.py").unlink()
    result = apply_plan(plan, tmp_path, project.config)
    assert result["applied"] == 1
    assert result["changed_files"] == 0
