"""Golden output tests for deterministic CLI commands.

Each golden file captures the exact stdout of a command run against a fresh
auth fixture repo.  Only known-nondeterministic spans are normalized before
comparison: git commit shas (40 hex chars) become ``<sha>``.  The fixture
identity is fixed by ``make_git_repo``, so everything else is byte-stable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.integration._fixtures import (
    change_requirement,
    run_trace,
    setup_auth_repo,
)

GOLDEN_DIR = Path(__file__).resolve().parent
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")

FRESH_CASES = [
    ("status_json.golden", ("status", "--json"), 0),
    ("context_impl_json.golden", ("context", "impl.auth.refresh", "--json"), 0),
    ("verify_pass_json.golden", ("verify", "--all", "--json"), 0),
]


def _normalize(text: str) -> str:
    """Replace commit shas with a stable placeholder."""
    return SHA_RE.sub("<sha>", text)


@pytest.mark.parametrize("golden,args,rc", FRESH_CASES)
def test_golden_fresh_repo(tmp_path, golden, args, rc):
    root = setup_auth_repo(tmp_path)
    proc = run_trace(root, *args)
    assert proc.returncode == rc
    assert _normalize(proc.stdout) == (GOLDEN_DIR / golden).read_text(encoding="utf-8")


def test_golden_verify_merge_stale(tmp_path):
    """verify --json at merge with a stale requirement change: the TL011 /
    TL110 / TL021 diagnostics render exactly (shas normalized)."""
    root = setup_auth_repo(tmp_path)
    change_requirement(root)
    assert run_trace(root, "index", "--changed").returncode == 0

    proc = run_trace(root, "verify", "--all", "--lifecycle", "merge", "--json")
    assert proc.returncode == 1
    assert _normalize(proc.stdout) == (
        GOLDEN_DIR / "verify_merge_stale_json.golden"
    ).read_text(encoding="utf-8")
