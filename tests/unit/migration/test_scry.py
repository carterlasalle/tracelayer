"""Tests for scry annotation detection (spec 33 adoption, contract §M).

v1 is detection only: ``scry:inline`` / ``scry:artifact`` annotations are
reported for manual review; nothing is auto-applied and no files change.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from tracelayer.config import Project, TraceConfig
from tracelayer.diagnostics import SEVERITY_INFO
from tracelayer.migration import scry


# trace:v1 id=test.dogfood.tests.unit.migration.test_scry.py type=test
def make_project(root: Path) -> Project:
    return Project(root=root, config=TraceConfig(repo_id="probe"))


def test_scan_scry_detects_inline_and_artifact_sorted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f(): pass  # scry:inline capture\n")
    (tmp_path / "docs.md").write_text("scry:artifact capture\nplain line\nscry:inline second\n")
    records, _ = scan_scry_out(tmp_path)
    assert records == [
        {"path": "docs.md", "line": 1, "raw": "scry:artifact capture", "kind": "artifact"},
        {"path": "docs.md", "line": 3, "raw": "scry:inline second", "kind": "inline"},
        {
            "path": "src/a.py",
            "line": 1,
            "raw": "def f(): pass  # scry:inline capture",
            "kind": "inline",
        },
    ]


def test_scan_scry_reports_info_diagnostics_per_annotation(tmp_path: Path) -> None:
    (tmp_path / "docs.md").write_text("scry:inline one\nscry:inline two\n")
    records, diags = scan_scry_out(tmp_path)
    assert len(records) == 2
    assert len(diags) == 2
    for d in diags:
        assert d.rule_id == "TL040"
        assert d.severity == SEVERITY_INFO
        assert d.path == "docs.md"
        assert "manual migration review required" in d.message
    assert sorted(d.line or 0 for d in diags) == [1, 2]


def test_scan_scry_is_detection_only_no_apply(tmp_path: Path) -> None:
    f = tmp_path / "docs.md"
    f.write_text("scry:inline keep me\n")
    records, _ = scan_scry_out(tmp_path)
    assert records and records[0]["kind"] == "inline"
    # v1 exposes no apply surface at all.
    functions = {name for name, _ in inspect.getmembers(scry, inspect.isfunction)}
    assert not any(name.startswith("apply") for name in functions)
    # And the scan itself never rewrites files.
    assert f.read_text() == "scry:inline keep me\n"


def test_scan_scry_skips_binary_files(tmp_path: Path) -> None:
    (tmp_path / "blob.dat").write_bytes(b"\x00\x01scry:inline hidden")
    (tmp_path / "ok.md").write_text("scry:inline visible\n")
    records, _ = scan_scry_out(tmp_path)
    assert [r["path"] for r in records] == ["ok.md"]


def scan_scry_out(tmp_path: Path):
    project = make_project(tmp_path)
    return scry.scan_scry(tmp_path, project.config)
