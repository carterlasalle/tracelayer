"""Release artifact inspection: what ships is what should ship (spec Section 60).

Inspects built wheels/sdists for must-include content (source, skills,
adapters, docs) and must-exclude junk (caches, venvs, session state,
coverage). Static by default; building and smoke-installing are opt-in.
"""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path

# Wheel-internal prefixes that must be present (spec 60 must-include).
WHEEL_MUST_INCLUDE = (
    "tracelayer/__init__.py",
    "tracelayer/_skills/traceability/",
    "tracelayer/_adapters/",
)

# Sdist top-level members that must be present.
SDIST_MUST_INCLUDE = (
    "pyproject.toml",
    "README.md",
    "src/tracelayer/__init__.py",
    "skills/traceability/SKILL.md",
    "adapters/oh-my-pi/package.json",
    "docs/marker-protocol.md",
)

# Fragments that must never ship (spec 60 must-exclude).
FORBIDDEN_FRAGMENTS = (
    ".trace/var",
    ".trace/cache",
    ".trace/sessions",
    ".trace/receipts",
    ".venv",
    "node_modules",
    ".coverage",
    "coverage.xml",
    "htmlcov",
    ".pytest_cache",
    "__pycache__",
    ".hypothesis",
    "junit.xml",
)


# trace:exempt reason=internal-helper
def _names(path: Path) -> list[str]:
    """Archive member names for wheels, sdists, and tars."""
    text = path.suffixes[-2:]
    if text == [".tar", ".gz"] or path.suffix in (".tgz", ".tar"):
        import tarfile

        with tarfile.open(path, "r:*") as archive:
            return archive.getnames()
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


# trace:exempt reason=internal-helper
def _project_version(root: Path | str) -> str | None:
    """Declared release version from pyproject.toml."""
    try:
        data = tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    return str(version) if version else None


# trace:v1 id=impl.release.inspect work=WORK-centralized-release-artifact-checks satisfies=REQ-distribution-inspector
def inspect_artifact(path: Path | str) -> dict:
    """Check one wheel/sdist against the must-include/must-exclude lists."""
    path = Path(path)
    try:
        names = _names(path)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return {"file": path.name, "ok": False, "error": str(exc)[:200]}
    is_wheel = path.suffix == ".whl"
    stripped = [n.split("/", 1)[-1] if "/" in n and not is_wheel else n for n in names]
    required = WHEEL_MUST_INCLUDE if is_wheel else SDIST_MUST_INCLUDE
    missing = [r for r in required if not any(n == r or n.startswith(r) for n in stripped)]
    forbidden = sorted({f for f in FORBIDDEN_FRAGMENTS for n in names if f in n})
    return {
        "file": path.name,
        "ok": not missing and not forbidden,
        "missing": missing,
        "forbidden": forbidden,
    }


# trace:v1 id=impl.release.check-dist work=WORK-centralized-release-artifact-checks satisfies=REQ-release-check-command
def check_dists(dist_dir: Path | str) -> dict:
    """Inspect every wheel/sdist in a directory; ok only when all pass."""
    dist_dir = Path(dist_dir)
    try:
        artifacts = sorted(
            p
            for p in dist_dir.iterdir()
            if p.suffix in (".whl", ".gz") or p.suffixes[-2:] == [".tar", ".gz"]
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200], "artifacts": []}
    results = [inspect_artifact(p) for p in artifacts]
    if not results:
        return {"ok": False, "error": "no wheels or sdists found", "artifacts": []}
    return {"ok": all(r["ok"] for r in results), "artifacts": results}
