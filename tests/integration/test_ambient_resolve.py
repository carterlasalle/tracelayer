"""Ambient Trace Phases 2+3: NL resolution and spec evolution acceptance tests."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace

BUNDLE = {
    "title": "Local repository dependency scanner",
    "kind": "greenfield_project",
    "intent": "Find Git repositories with oversized node_modules directories",
    "requirements": [
        {
            "title": "Repository discovery",
            "statement": "Discover Git repositories under configured roots.",
            "acceptance": ["repos under each configured root are found"],
        },
        {"title": "node_modules measurement", "statement": "Measure node_modules disk usage."},
    ],
}


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve type=test
def _bootstrapped(tmp_path, session="a"):
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": session}, input=json.dumps(BUNDLE))
    return repo


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_resolve_continues_existing_work_in_new_session type=test
def test_resolve_continues_existing_work_in_new_session(tmp_path):
    """'continue the scanner' in a fresh session resolves without IDs."""
    repo = _bootstrapped(tmp_path, "creator")
    run_trace(repo, "task", "end", env={"TRACE_SESSION": "creator"})
    r = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "continue the scanner and make it faster",
        env={"TRACE_SESSION": "new-session"},
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "resume"
    assert result["work"] == "WORK-local-repository-dependency-scanner"
    assert set(result["requirements"]) == {
        "REQ-repository-discovery",
        "REQ-node-modules-measurement",
    }
    assert result["confidence"] >= 0.55


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_resolve_pronoun_reference_uses_active_session type=test
def test_resolve_pronoun_reference_uses_active_session(tmp_path):
    """'this thing' resolves via the active session context (signal A)."""
    repo = _bootstrapped(tmp_path, "s")
    r = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "for this thing add csv output",
        env={"TRACE_SESSION": "s"},
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "resume"
    assert result["confidence"] == 1.0
    assert result["source"] == ["active_session"]


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_resolve_new_when_no_match type=test
def test_resolve_new_when_no_match(tmp_path):
    repo = _bootstrapped(tmp_path, "a")
    r = run_trace(
        repo, "task", "resolve", "--prompt", "build a password manager", env={"TRACE_SESSION": "b"}
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "new"
    assert result["work"] is None


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_bootstrap_persists_acceptance_criteria type=test
def test_bootstrap_persists_acceptance_criteria(tmp_path):
    """Phase 3: acceptance criteria written into the spec."""
    repo = _bootstrapped(tmp_path, "a")
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert "- acceptance: repos under each configured root are found" in spec


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_requirement_revision_stales_verification type=test
def test_requirement_revision_stales_verification(tmp_path):
    """Phase 3: editing a requirement in the spec changes its fingerprint
    and marks downstream implementations stale (no IDs needed)."""
    repo = _bootstrapped(tmp_path, "a")
    (repo / "scanner.py").write_text(
        "# trace:v1 id=impl.scanner.discover satisfies=REQ-repository-discovery work=WORK-local-repository-dependency-scanner\n"
        "def discover():\n    return []\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    # revise the requirement text in the spec
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    spec = spec.replace(
        "Discover Git repositories under configured roots.",
        "Discover Git repositories under configured roots, including submodules.",
    )
    (repo / "docs" / "spec.md").write_text(spec, encoding="utf-8")
    run_trace(repo, "index", "--changed")
    status = json.loads(run_trace(repo, "status", "--json").stdout)
    assert status["blocking_stale"] >= 1  # impl is stale after the requirement revision


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_finish_auto_marks_work_done type=test
def test_finish_auto_marks_work_done(tmp_path):
    """Phase 5: finish --auto marks the work done and clears the session."""
    repo = _bootstrapped(tmp_path, "f")
    r = run_trace(repo, "task", "finish", env={"TRACE_SESSION": "f"})
    result = json.loads(r.stdout)
    assert result["status"] == "done"
    work_toml = (repo / ".trace" / "work.toml").read_text(encoding="utf-8")
    assert 'status = "done"' in work_toml
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "f"}).stdout)
    assert ctx["work"] is None  # session cleared


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_finish_blocked_with_pending_obligations type=test
def test_finish_blocked_with_pending_obligations(tmp_path):
    """Phase 5: unresolved authoring obligations block completion."""
    repo = _bootstrapped(tmp_path, "f")
    run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "f"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "src" / "new.py"),
                    "content": "def new_behavior():\n    return 1\n",
                },
            }
        ),
    )
    r = run_trace(repo, "task", "finish", env={"TRACE_SESSION": "f"})
    result = json.loads(r.stdout)
    assert result["status"] == "blocked"
    assert result["pending_obligations"] >= 1


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_mutation_receipts_recorded type=test
def test_mutation_receipts_recorded(tmp_path):
    """Phase 5: post-mutation writes a receipt under the active work."""
    repo = _bootstrapped(tmp_path, "f")
    (repo / "scanner.py").write_text(
        "# trace:v1 id=impl.scanner.discover satisfies=REQ-repository-discovery work=WORK-local-repository-dependency-scanner\n"
        "def discover():\n    return []\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "f"},
        input=json.dumps({"path": "scanner.py"}),
    )
    receipts = (repo / ".trace" / "receipts" / "receipts.jsonl").read_text(encoding="utf-8")
    assert "WORK-local-repository-dependency-scanner" in receipts
    assert "impl.scanner.discover" in receipts


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_open_work_priority_in_resolution type=test
def test_open_work_priority_in_resolution(tmp_path):
    """Phase 6: open (active) work outranks completed work on a match."""
    repo = _bootstrapped(tmp_path, "a")
    run_trace(repo, "task", "finish", env={"TRACE_SESSION": "a"})  # close it
    # create a second, open work with a matching concept
    run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "a"},
        input=json.dumps(
            {
                "title": "scanner performance tuning",
                "kind": "feature_extension",
                "intent": "speed up",
                "requirements": [{"title": "Faster scanning", "statement": "Scan faster."}],
            }
        ),
    )
    r = run_trace(
        repo, "task", "resolve", "--prompt", "work on the scanner", env={"TRACE_SESSION": "other"}
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "resume"
    assert result["work"] == "WORK-scanner-performance-tuning"  # the OPEN one wins


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_continuity_via_branch_name type=test
def test_continuity_via_branch_name(tmp_path):
    """Phase 6: a branch matching the work slug is a continuity signal."""
    import subprocess

    repo = _bootstrapped(tmp_path, "a")
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "scanner-refactor"], check=True)
    r = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "refactor the scanner internals",
        env={"TRACE_SESSION": "new-session"},
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "resume"
    assert "branch" in result["source"]


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_refactor_preserves_identity_and_invents_no_requirement type=test
def test_refactor_preserves_identity_and_invents_no_requirement(tmp_path):
    """§55 Refactor: new work, same implementation identity, no invented req."""
    repo = _bootstrapped(tmp_path, "r")
    (repo / "scanner.py").write_text(
        "# trace:v1 id=impl.scanner.discover satisfies=REQ-repository-discovery work=WORK-local-repository-dependency-scanner\n"
        "def discover():\n    return []\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    run_trace(repo, "task", "finish", env={"TRACE_SESSION": "r"})
    # refactor: rewrite the impl with the same semantic identity
    (repo / "scanner.py").write_text(
        "# trace:v1 id=impl.scanner.discover satisfies=REQ-repository-discovery work=WORK-local-repository-dependency-scanner\n"
        "def discover():\n    return iter([])  # streamed\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    # identity preserved
    ctx = json.loads(run_trace(repo, "context", "impl.scanner.discover", "--json").stdout)
    assert ctx["node_type"] == "implementation"
    assert ctx["symbol"] == "scanner.discover"
    # no new requirement invented: the graph still has only the original two
    status = json.loads(run_trace(repo, "status", "--json").stdout)
    assert status["nodes"] <= 6  # work + 2 reqs + plan + impl (no extra REQ)


# trace:v1 id=test.dogfood.tests.integration.test_ambient_resolve.test_bug_fix_backfills_missing_contract type=test
def test_bug_fix_backfills_missing_contract(tmp_path):
    """§55 Bug fix: existing requirement reused; a missing contract can be
    backfilled (the agent creates the new REQ via bootstrap-style edit)."""
    repo = _bootstrapped(tmp_path, "b")
    (repo / "scanner.py").write_text(
        "# trace:v1 id=impl.scanner.discover satisfies=REQ-repository-discovery work=WORK-local-repository-dependency-scanner\n"
        "def discover():\n    return []\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    # user reports a symlink bug: backfill the contract in the spec
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    spec += (
        "\n### REQ-symlink-size-semantics — Symlink size semantics\n\n"
        "<!-- trace:v1 id=REQ-symlink-size-semantics type=requirement work=WORK-local-repository-dependency-scanner -->\n\n"
        "Symlinked node_modules must not be double-counted.\n"
    )
    (repo / "docs" / "spec.md").write_text(spec, encoding="utf-8")
    run_trace(repo, "index", "--all")
    ctx = json.loads(run_trace(repo, "context", "REQ-symlink-size-semantics", "--json").stdout)
    assert ctx["node_type"] == "requirement"
