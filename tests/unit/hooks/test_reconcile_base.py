"""Reconciliation drops obligations for boundaries unchanged since the base."""

from __future__ import annotations

import subprocess

from tracelayer.hooks.post_mutation import _resolve_obligations_in


def _git(root, *args) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def _commit(root, text: str) -> None:
    (root / "mod.py").write_text(text, encoding="utf-8")
    _git(root, "add", "mod.py")
    _git(root, "commit", "-qm", "x")


# trace:v1 id=test.hooks.reconcile-base type=test verifies=REQ-base-fingerprint-reconciliation
def test_unchanged_boundary_resolves_changed_stays(project, state) -> None:
    _git(project.root, "init", "-q")
    _git(project.root, "config", "commit.gpgsign", "false")
    _commit(project.root, "def helper():\n    return 1\n\n\ndef target():\n    return 1\n")
    _commit(project.root, "def helper():\n    return 1\n\n\ndef target():\n    return 2\n")
    text = (project.root / "mod.py").read_text(encoding="utf-8")
    for symbol in ("mod.helper", "mod.target"):
        state.add_obligation(
            "s1",
            {
                "path": "mod.py",
                "symbol": symbol,
                "kind": "new_behavior",
                "work": "",
                "requirement": "",
                "suggested_marker": "",
                "state": "pending",
            },
        )
    resolved = _resolve_obligations_in(state, "s1", project, "mod.py", text)
    remaining = {(o["path"], o["symbol"]) for o in state.pending_obligations("s1")}
    assert resolved == 1
    assert remaining == {("mod.py", "mod.target")}


# trace:v1 id=test.hooks.reconcile-markdown type=test verifies=REQ-base-fingerprint-reconciliation
def test_markdown_heading_with_marker_resolves(project, state) -> None:
    text = "# Canonical facts\n\n<!-- trace:v1 id=doc.canonical-facts work=WORK-X -->\n"
    (project.root / "notes.md").write_text(text, encoding="utf-8")
    state.add_obligation(
        "s1",
        {
            "path": "notes.md",
            "symbol": "Canonical facts",
            "kind": "new_behavior",
            "work": "",
            "requirement": "",
            "suggested_marker": "<!-- trace:v1 id=doc.canonical-facts -->",
            "state": "pending",
        },
    )
    resolved = _resolve_obligations_in(state, "s1", project, "notes.md", text)
    assert resolved == 1
    assert state.pending_obligations("s1") == []
