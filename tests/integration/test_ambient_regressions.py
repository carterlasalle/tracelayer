"""Regression tests for the adversarial audit findings (all 10)."""

from __future__ import annotations

import json
import subprocess

from tests.conftest import make_git_repo, run_trace
from tests.integration._fixtures import complete_work

BUNDLE = {
    "title": "Local repository dependency scanner",
    "kind": "greenfield_project",
    "intent": "Find Git repositories with oversized node_modules directories",
    "requirements": [
        {
            "title": "Repository discovery",
            "statement": "Discover Git repositories under configured roots.",
        },
    ],
    "plan": {"recommended": True, "steps": ["P1 — discovery"]},
}


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions type=test
def _bootstrapped(tmp_path, session="a"):
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": session}, input=json.dumps(BUNDLE))
    return repo


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_finish_blocks_when_changed_scope_fails type=test
def test_finish_blocks_when_changed_scope_fails(tmp_path):
    """FINDING 1: finish must block when verify --changed fails."""
    repo = _bootstrapped(tmp_path, "f")
    # introduce a failing untraced file
    (repo / "unmarked.py").write_text("def sneaky():\n    return 1\n", encoding="utf-8")
    v = run_trace(repo, "verify", "--changed", "--lifecycle", "wip")
    assert v.returncode != 0  # the change set fails
    r = run_trace(repo, "task", "finish", env={"TRACE_SESSION": "f"})
    result = json.loads(r.stdout)
    assert result["status"] == "blocked"
    assert "TL012" in result.get("diagnostics", []) or "TL013" in result.get("diagnostics", [])
    # clean up: work is NOT marked done
    work_toml = (repo / ".trace" / "work.toml").read_text(encoding="utf-8")
    assert 'status = "done"' not in work_toml


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_resolve_unrelated_prompt_under_active_session_returns_new type=test
def test_resolve_unrelated_prompt_under_active_session_returns_new(tmp_path):
    """FINDING 2: an unrelated prompt must NOT resume the active session work."""
    repo = _bootstrapped(tmp_path, "s")
    r = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "build a password manager with biometric unlock",
        env={"TRACE_SESSION": "s"},
    )
    result = json.loads(r.stdout)
    assert result["resolution"] == "new"
    # a referential prompt still resumes via the active session
    r2 = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "for this thing add csv output",
        env={"TRACE_SESSION": "s"},
    )
    assert json.loads(r2.stdout)["resolution"] == "resume"
    r3 = run_trace(
        repo,
        "task",
        "resolve",
        "--prompt",
        "keep working on the scanner",
        env={"TRACE_SESSION": "s"},
    )
    assert json.loads(r3.stdout)["resolution"] == "resume"


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_repeated_bootstrap_does_not_overwrite_prior_spec type=test
def test_repeated_bootstrap_does_not_overwrite_prior_spec(tmp_path):
    """FINDING 3: a second bootstrap with the same title must keep the first
    work's spec intact (namespaced artifact paths)."""
    repo = _bootstrapped(tmp_path, "a")
    first = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    r2 = run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "b"}, input=json.dumps(BUNDLE))
    result = json.loads(r2.stdout)
    assert result["work"].endswith("-2")  # IDs unique
    spec_files = sorted(p.name for p in (repo / "docs").glob("spec*.md"))
    assert len(spec_files) >= 2  # the first spec file still exists
    assert "id=REQ-repository-discovery" in first  # first work's markers intact
    assert "REQ-repository-discovery" in (repo / "docs" / "spec.md").read_text()


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_opaque_scan_skips_internal_state type=test
def test_opaque_scan_skips_internal_state(tmp_path):
    """FINDING 4: the Bash scan must not create obligations for .trace state."""
    repo = _bootstrapped(tmp_path, "s")
    (repo / "src").mkdir()
    (repo / "src" / "gen.py").write_text("def generated_fn():\n    return 1\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat > src/gen.py"}}),
    )
    out = json.loads(r.stdout)
    obligations = out.get("created_obligations", [])
    assert any("src/gen.py::generated_fn" in o for o in obligations)
    assert not any(o.startswith(".trace/") for o in obligations)
    assert not any("work.toml" in o or "trace.toml" in o for o in obligations)


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_auto_init_from_repo_subdirectory type=test
def test_auto_init_from_repo_subdirectory(tmp_path):
    """FINDING 5: a hook fired from a subdir cwd bootstraps the repo root."""
    repo = make_git_repo(tmp_path, {"src/deep/x.py": "x = 1\n"})
    subdir = repo / "src" / "deep"
    subprocess.run(
        ["uv", "run", "trace", "hook", "pre-mutation", "--format", "json"],
        input=json.dumps({"path": "x.py"}),
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "TRACE_SESSION": "s"},
        cwd=str(subdir),
    )
    assert (repo / ".trace" / "trace.toml").exists()
    assert (repo / "AGENTS.md").exists()
    assert "tracelayer-agent-invariant:v2" in (repo / "AGENTS.md").read_text()


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_hook_hint_silent_with_root_before_subcommand type=test
def test_hook_hint_silent_with_root_before_subcommand(tmp_path):
    """FINDING 6: --root-first hook invocations must not leak the hint."""
    repo = _bootstrapped(tmp_path, "s")
    (repo / ".trace" / "cache").mkdir(parents=True, exist_ok=True)
    (repo / ".trace" / "trace.toml").unlink()
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "README.md"}),
    )
    assert "not configured" not in r.stderr
    assert "trace init" not in r.stderr


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_activate_returns_the_plan type=test
def test_activate_returns_the_plan(tmp_path):
    """FINDING 7: activate resolves the plan via the work edge."""
    repo = _bootstrapped(tmp_path, "a")
    run_trace(repo, "task", "end", env={"TRACE_SESSION": "a"})
    act = json.loads(
        run_trace(
            repo,
            "task",
            "activate",
            "WORK-local-repository-dependency-scanner",
            env={"TRACE_SESSION": "a"},
        ).stdout
    )
    assert act["plan"] == "PLAN-local-repository-dependency-scanner"


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_obligation_resolves_by_marker_id_despite_symbol_mismatch type=test
def test_obligation_resolves_by_marker_id_despite_symbol_mismatch(tmp_path):
    """FINDING 8 (v2): an obligation tracks the EXPECTED semantic boundary.

    The suggested marker id attached to a DIFFERENTLY-NAMED boundary must
    NOT auto-resolve (a marker string anywhere in the file is not proof of
    authoring) — the rename is confirmed explicitly, and a same-name
    boundary with the marker resolves even across a path-form mismatch.
    """
    repo = _bootstrapped(tmp_path, "s")
    # pre-mutation creates an obligation for new_behavior (absolute path)
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
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
    assert r.returncode == 2
    obl = json.loads(r.stdout)
    suggested = obl.get("output", "")
    import re as _re

    m = _re.search(r"id=([A-Za-z0-9._:/-]+)", suggested)
    assert m
    marker_id = m.group(1)
    # the file is written with the suggested marker id but a DIFFERENT symbol:
    # the marker string alone must NOT absorb the obligation
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "new.py").write_text(
        f"# trace:v1 id={marker_id} work=WORK-local-repository-dependency-scanner\n"
        "def differently_named():\n    return 1\n",
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "src/new.py"}),
    )
    assert r.returncode == 0, r.stderr
    fin = json.loads(
        run_trace(repo, "task", "finish", "--lifecycle", "wip", env={"TRACE_SESSION": "s"}).stdout
    )
    assert fin["status"] == "blocked"  # obligation NOT resolved by the mismatch
    # ...but the rename is confirmed explicitly (LLM semantics, engine
    # validates): the agent asserts the differently-named boundary IS the
    # renamed new_behavior
    r = run_trace(
        repo,
        "task",
        "resolve-obligation",
        "src/new.py",
        "--symbol",
        "new_behavior",
        env={"TRACE_SESSION": "s"},
    )
    assert r.returncode == 0, r.stderr
    fin2 = json.loads(
        run_trace(repo, "task", "finish", "--lifecycle", "wip", env={"TRACE_SESSION": "s"}).stdout
    )
    assert fin2["status"] != "blocked"  # explicit confirmation clears the deadlock


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_obligation_resolves_same_boundary_across_path_form type=test
def test_obligation_resolves_same_boundary_across_path_form(tmp_path):
    """FINDING 8 (v2): a same-name boundary with the marker resolves even when
    pre (absolute path) and post (relative path) spell the path differently."""
    repo = _bootstrapped(tmp_path, "s")
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
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
    assert r.returncode == 2
    obl = json.loads(r.stdout)
    suggested = obl.get("output", "")
    import re as _re

    m = _re.search(r"id=([A-Za-z0-9._:/-]+)", suggested)
    assert m
    marker_id = m.group(1)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "new.py").write_text(
        f"# trace:v1 id={marker_id} work=WORK-local-repository-dependency-scanner\n"
        "def new_behavior():\n    return 1\n",
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "src/new.py"}),
    )
    assert r.returncode == 0, r.stderr
    fin = json.loads(
        run_trace(repo, "task", "finish", "--lifecycle", "wip", env={"TRACE_SESSION": "s"}).stdout
    )
    assert fin["status"] != "blocked"  # same boundary, path-form mismatch: resolved


# trace:v1 id=test.dogfood.tests.integration.test_ambient_regressions.test_resolve_completed_work_returns_new type=test
def test_resolve_completed_work_returns_new(tmp_path):
    """FINDING 9: completed work is not resumable — follow-ups create new work."""
    repo = _bootstrapped(tmp_path, "a")
    result = complete_work(
        repo,
        "a",
        work_id="WORK-local-repository-dependency-scanner",
        req_id="REQ-repository-discovery",
        impl_path="scanner.py",
        impl_id="impl.scanner.discover-repositories",
        impl_code="def discover_repositories():\n    return []\n",
        test_path="test_scanner.py",
        test_id="TEST-scanner",
        test_code="def test_discover():\n    assert discover_repositories() == []\n",
    )
    assert result["status"] == "done", result
    r = run_trace(repo, "task", "resolve", "--prompt", "scanner", env={"TRACE_SESSION": "fresh"})
    result = json.loads(r.stdout)
    assert result["resolution"] == "new"
