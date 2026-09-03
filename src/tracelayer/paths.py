"""Filesystem classification registry: one source for hygiene (spec Section 58).

Every repository path belongs to one class. Untracked repo-local classes
declare the .gitignore entries that must cover them; ``gitignore_gaps``
reports drift. Global installs live outside the repository by definition.
"""

from __future__ import annotations

from pathlib import Path

CLASSES = (
    "CANONICAL_SOURCE",
    "TRACKED_PROJECT_STATE",
    "LOCAL_PROJECT_STATE",
    "GLOBAL_AGENT_INSTALLATION",
    "CACHE",
    "GENERATED_OUTPUT",
    "TEST_OUTPUT",
    "BUILD_OUTPUT",
    "EXTERNAL_TOOL_STATE",
)

_PATH_CLASSES: dict[str, dict] = {
    "CANONICAL_SOURCE": {
        "tracked": True,
        "gitignore": [],
        "description": "Hand-written source, skills, adapters, tests.",
    },
    "TRACKED_PROJECT_STATE": {
        "tracked": True,
        "gitignore": [],
        "description": "Committed trace state: work, policy, specs, plans, generated docs.",
    },
    "LOCAL_PROJECT_STATE": {
        "tracked": False,
        "gitignore": [".trace/cache/", ".trace/var/"],
        "description": "Machine-local trace runtime: caches, sessions, receipts, indexes.",
    },
    "GLOBAL_AGENT_INSTALLATION": {
        "tracked": False,
        "gitignore": [],
        "description": "User-wide agent dirs (~/.claude, ~/.omp/agent, ...); outside the repo.",
    },
    "CACHE": {
        "tracked": False,
        "gitignore": [
            "__pycache__/",
            "*.pyc",
            ".ruff_cache/",
            ".pytest_cache/",
            ".mypy_cache/",
            ".hypothesis/",
        ],
        "description": "Regenerable tool caches.",
    },
    "GENERATED_OUTPUT": {
        "tracked": True,
        "gitignore": [],
        "description": "Generated docs committed as source; refresh via trace docs generate.",
    },
    "TEST_OUTPUT": {
        "tracked": False,
        "gitignore": ["junit.xml", "coverage.xml", "coverage.lcov", "htmlcov/", ".coverage"],
        "description": "Test and coverage reports.",
    },
    "BUILD_OUTPUT": {
        "tracked": False,
        "gitignore": ["dist/", "build/", ".build/", "*.egg-info/", "*.whl", "*.tar.gz"],
        "description": "Packaging and build products.",
    },
    "EXTERNAL_TOOL_STATE": {
        "tracked": False,
        "gitignore": [".venv/", "node_modules/", ".serena/"],
        "description": "Third-party tool environments and checkpoints.",
    },
}


# trace:exempt reason=internal-helper
def path_classes() -> list[str]:
    """Registered filesystem classes in stable order."""
    return list(CLASSES)


# trace:exempt reason=internal-helper
def class_info(name: str) -> dict | None:
    """Registry entry for ``name``, or None when unregistered."""
    entry = _PATH_CLASSES.get(str(name or "").upper())
    return dict(entry) if entry is not None else None


# trace:exempt reason=internal-helper
def required_gitignore_entries() -> list[str]:
    """Sorted .gitignore entries covering every untracked repo-local class."""
    entries: set[str] = set()
    for name, entry in _PATH_CLASSES.items():
        if name == "GLOBAL_AGENT_INSTALLATION" or entry["tracked"]:
            continue
        entries.update(entry["gitignore"])
    return sorted(entries)


# trace:v1 id=impl.paths.gaps work=WORK-global-setup-filesystem-hygiene-web-work-view-and-skill-refresh satisfies=REQ-filesystem-classification-registry
def gitignore_gaps(root: Path | str) -> list[str]:
    """Required entries missing from the repository .gitignore."""
    ignore = Path(root) / ".gitignore"
    content = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    return [entry for entry in required_gitignore_entries() if entry not in content]
