"""Per-test coverage adapter (contract §E, spec 17.7 / 25.2 level 2).

Uses a fake `coverage` package to exercise the adapter's mapping logic
deterministically.  When the real coverage package is unavailable the
import-guard path (RuntimeError) is still exercised; the rest is skipped.
"""

from __future__ import annotations

import types

import pytest

from tracelayer.evidence.models import entity_uid_for
from tracelayer.evidence.per_test import (
    _context_to_framework_id,
    collect_pytest_per_test,
    implementation_uid_for,
)

coverage_pkg = pytest.importorskip("coverage", reason="coverage not installed")


class FakeCoverageData:
    """Minimal stand-in for coverage.CoverageData's read API."""

    def __init__(self, basename: str):
        self._basename = basename

    def read(self) -> None:
        return None

    def measured_contexts(self) -> list[str]:
        return [
            "tests/app/test_a.py::test_one",
            "tests/app/test_a.py::test_two[param-1]",
            "tests/app/test_a.py::test_two[param-2]",
            "tests/app/test_untraced.py::test_skip",  # no test_id_map entry
        ]

    def measured_files(self) -> list[str]:
        return ["src/app.py", "src/untouched.py"]

    def contexts_by_lineno(self, filename: str) -> dict[int, list[str]]:
        if filename != "src/app.py":
            return {}
        return {
            11: ["tests/app/test_a.py::test_one"],
            12: [
                "tests/app/test_a.py::test_two[param-1]",
                "tests/app/test_a.py::test_two[param-2]",
            ],
            13: ["tests/app/test_a.py::test_one"],
            99: ["tests/app/test_a.py::test_one"],  # outside impl range
        }


def _fake_module() -> types.ModuleType:
    mod = types.ModuleType("coverage")
    mod.CoverageData = FakeCoverageData
    return mod


@pytest.fixture
def fake_coverage(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "coverage", _fake_module())
    yield FakeCoverageData


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("tests/auth/test_refresh.py::test_reuse", "tests.auth.test_refresh.test_reuse"),
        (
            "tests/auth/test_refresh.py::test_reuse[param-1]",
            "tests.auth.test_refresh.test_reuse",
        ),
        (
            "tests/api/test_auth.py::TestAuth::test_login",
            "tests.api.test_auth.TestAuth.test_login",
        ),
        ("tests/deep/nested/test_x.py::test_y", "tests.deep.nested.test_x.test_y"),
    ],
)
def test_context_to_framework_id_pytest_convention(context: str, expected: str):
    assert _context_to_framework_id(context) == expected


def test_implementation_uid_for_is_stable():
    assert implementation_uid_for("src/app.py") == implementation_uid_for("src/app.py")
    assert implementation_uid_for("src/app.py") != implementation_uid_for("src/other.py")


def test_per_test_collection_maps_contexts_to_test_uids(fake_coverage, tmp_path):
    db = tmp_path / "coverage.db"
    db.write_bytes(b"fake coverage sqlite")
    records = collect_pytest_per_test(
        coverage_db=str(db),
        impl_symbols={"src/app.py": (10, 20)},
        test_id_map={
            "tests.app.test_a.test_one": "TEST:ONE",
            "tests.app.test_a.test_two": "TEST:TWO",
        },
    )
    by_impl = {r.test_uid: r for r in records}
    # test_one executed lines 11 and 13 inside the range -> 2 hits
    assert by_impl[entity_uid_for("TEST:ONE")].hit_count == 2
    # both parametrized variants map to the same test and sum hits -> 2
    assert by_impl[entity_uid_for("TEST:TWO")].hit_count == 2
    # unmapped test contexts produce no record; line 99 is out of range
    assert len(records) == 2
    for r in records:
        assert r.coverage_kind == "per_test"
        assert r.implementation_uid == implementation_uid_for("src/app.py")


def test_per_test_collection_skips_unmapped_contexts(fake_coverage, tmp_path):
    db = tmp_path / "coverage.db"
    db.write_bytes(b"fake coverage sqlite")
    records = collect_pytest_per_test(
        coverage_db=str(db),
        impl_symbols={"src/app.py": (10, 20)},
        test_id_map={},  # nothing maps -> no records
    )
    assert records == []


def test_per_test_collection_missing_db_raises(fake_coverage):
    with pytest.raises(RuntimeError, match="coverage database not found"):
        collect_pytest_per_test(
            coverage_db="/nonexistent/path.db",
            impl_symbols={"src/app.py": (10, 20)},
            test_id_map={},
        )


def test_per_test_collection_unreadable_db_raises(fake_coverage, tmp_path):
    db = tmp_path / "coverage.db"
    db.write_text("not a sqlite file", encoding="utf-8")

    class BrokenCoverageData(FakeCoverageData):
        def read(self) -> None:
            import sqlite3

            raise sqlite3.DatabaseError("file is not a database")

    import sys

    mod = _fake_module()
    mod.CoverageData = BrokenCoverageData
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setitem(sys.modules, "coverage", mod)
    try:
        with pytest.raises(RuntimeError, match="cannot read coverage database"):
            collect_pytest_per_test(
                coverage_db=str(db),
                impl_symbols={"src/app.py": (10, 20)},
                test_id_map={},
            )
    finally:
        monkeypatch.undo()
