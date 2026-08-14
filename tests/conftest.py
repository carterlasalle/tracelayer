"""Shared fixtures for the TraceLayer test suite (owned by TestsCore).

Provides ``make_git_repo`` and ``run_trace`` per the batch contract, plus
convenience fixtures wrapping them and a ``graph_store`` fixture. All helpers
are deterministic: fixed git identity, no wall-clock dependence, tmp_path
isolation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


# trace:v1 id=test.dogfood.tests.conftest.py type=test
def make_git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a git repository at ``tmp_path`` with ``files`` committed.

    ``files`` maps POSIX relative paths to UTF-8 text content; parent
    directories are created as needed. The repo uses branch ``main`` and a
    fixed test identity so tests are deterministic without a global git
    config. Returns the repo root (``tmp_path``).
    """
    root = Path(tmp_path)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tests@tracelayer.local")
    _git(root, "config", "user.name", "TraceLayer Tests")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def run_trace(
    root: Path,
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the ``trace`` CLI against ``root``; returns the CompletedProcess.

    The binary is ``.venv/bin/trace`` (``sys.executable`` lives in
    ``.venv/bin``). ``--root <root>`` is always prepended to ``args``.
    ``env`` is merged over the current environment; ``input`` is piped to
    stdin. No output normalization is performed.
    """
    binary = Path(sys.executable).parent / "trace"
    cmd = [str(binary), "--root", str(root), *args]
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        input=input,
        capture_output=True,
        text=True,
        env=full_env,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """An empty git repository at ``tmp_path`` (no commits)."""
    root = Path(tmp_path)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tests@tracelayer.local")
    _git(root, "config", "user.name", "TraceLayer Tests")
    return root


@pytest.fixture
def graph_store(tmp_path: Path):
    """A fresh GraphStore at tmp_path/index.sqlite3 with FTS enabled."""
    from tracelayer.graph.store import GraphStore

    store = GraphStore.open(tmp_path / "index.sqlite3", fts=True)
    yield store
    store.close()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
