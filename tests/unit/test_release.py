"""Tests for release artifact inspection (spec Section 60)."""

from __future__ import annotations

import tarfile
import zipfile

from tracelayer.release import FORBIDDEN_FRAGMENTS, check_dists, inspect_artifact


def make_wheel(path, extra=()) -> None:
    names = [
        "tracelayer/__init__.py",
        "tracelayer/cli.py",
        "tracelayer/_skills/traceability/SKILL.md",
        "tracelayer/_adapters/omp/hook.py",
        *extra,
    ]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "# artifact\n")


def make_sdist(path, extra=()) -> None:
    names = [
        "tracelayer-0.2.40/pyproject.toml",
        "tracelayer-0.2.40/README.md",
        "tracelayer-0.2.40/src/tracelayer/__init__.py",
        "tracelayer-0.2.40/skills/traceability/SKILL.md",
        "tracelayer-0.2.40/adapters/oh-my-pi/package.json",
        "tracelayer-0.2.40/docs/marker-protocol.md",
        *extra,
    ]
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            import io

            data = b"# artifact\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


# trace:v1 id=test.release.inspect type=test verifies=REQ-release-check-command
def test_inspect_good_and_bad_wheels(tmp_path) -> None:
    good = tmp_path / "tracelayer-0.2.40-py3-none-any.whl"
    make_wheel(good)
    result = inspect_artifact(good)
    assert result["ok"] is True
    assert result["missing"] == [] and result["forbidden"] == []
    bad = tmp_path / "tracelayer-0.2.40-py3-none-win.whl"
    make_wheel(bad, extra=["tracelayer/__pycache__/cli.pyc", ".venv/lib/x.py"])
    result = inspect_artifact(bad)
    assert result["ok"] is False
    assert ".venv" in result["forbidden"]
    assert "__pycache__" in result["forbidden"]
    thin = tmp_path / "thin-0.1-py3-none-any.whl"
    with zipfile.ZipFile(thin, "w") as archive:
        archive.writestr("thin/__init__.py", "")
    assert inspect_artifact(thin)["missing"] != []


# trace:v1 id=test.release.sdist type=test verifies=REQ-release-check-command
def test_inspect_sdist_and_empty_dir(tmp_path) -> None:
    sdist = tmp_path / "tracelayer-0.2.40.tar.gz"
    make_sdist(sdist)
    assert inspect_artifact(sdist)["ok"] is True
    empty = tmp_path / "empty"
    empty.mkdir()
    assert check_dists(empty)["ok"] is False
    assert check_dists(tmp_path)["ok"] is True


# trace:v1 id=test.release.fragments type=test verifies=REQ-distribution-inspector
def test_forbidden_covers_runtime_junk() -> None:
    for fragment in (
        ".trace/var",
        ".venv",
        "node_modules",
        ".coverage",
        "coverage.xml",
        ".pytest_cache",
        "junit.xml",
    ):
        assert fragment in FORBIDDEN_FRAGMENTS
