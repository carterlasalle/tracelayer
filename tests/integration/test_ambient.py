"""Ambient Trace Phase 1: zero-ceremony bootstrap acceptance tests (spec §55)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace

SCANNER_BUNDLE = {
    "title": "Local repository dependency scanner",
    "kind": "greenfield_project",
    "intent": "Find Git repositories with oversized node_modules directories",
    "requirements": [
        {
            "title": "Repository discovery",
            "statement": "Discover Git repositories under configured roots.",
        },
        {"title": "node_modules measurement", "statement": "Measure node_modules disk usage."},
        {"title": "Configurable threshold", "statement": "Allow the threshold to be configured."},
        {"title": "Ranked results", "statement": "Return repositories ordered largest first."},
    ],
    "plan": {
        "recommended": True,
        "steps": ["P1 — CLI/configuration", "P2 — discovery", "P3 — measurement", "P4 — ranking"],
    },
}


# trace:v1 id=test.dogfood.tests.integration.test_ambient type=test
def _fresh_repo(tmp_path):
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    return repo


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_greenfield_bootstrap_creates_work_spec_reqs_plan type=test
def test_greenfield_bootstrap_creates_work_spec_reqs_plan(tmp_path):
    """The full greenfield lifecycle from one semantic bundle: no IDs needed."""
    repo = _fresh_repo(tmp_path)
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "ambient-s"},
        input=json.dumps(SCANNER_BUNDLE),
    )
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["work"] == "WORK-local-repository-dependency-scanner"
    assert len(result["requirements"]) == 4
    assert result["plan"] == "PLAN-local-repository-dependency-scanner"
    # spec persisted with requirement markers
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert "id=REQ-repository-discovery" in spec
    assert "type=requirement work=WORK-local-repository-dependency-scanner" in spec
    # work item persisted with origin provenance
    work_toml = (repo / ".trace" / "work.toml").read_text(encoding="utf-8")
    assert "WORK-local-repository-dependency-scanner" in work_toml
    assert 'origin.session = "ambient-s"' in work_toml
    # plan persisted
    assert (repo / "docs" / "plan-local-repository-dependency-scanner.md").exists()
    # context activated with ALL requirements (plural)
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "ambient-s"}).stdout)
    assert ctx["work"] == result["work"]
    assert len(ctx["requirements"]) == 4
    assert ctx["plan"] == result["plan"]
    # graph indexes the artifacts
    status = json.loads(run_trace(repo, "status", "--json").stdout)
    assert status["nodes"] >= 6  # work + 4 reqs + plan
    assert status["broken_refs"] == 0


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_authoring_gate_uses_bootstrapped_requirements_without_ids type=test
def test_authoring_gate_uses_bootstrapped_requirements_without_ids(tmp_path):
    """After bootstrap, a Write is guided by the active requirements — the
    user never supplied an ID."""
    repo = _fresh_repo(tmp_path)
    run_trace(
        repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(SCANNER_BUNDLE)
    )
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
                    "file_path": str(repo / "scanner.py"),
                    "content": "def discover_repositories():\n    return []\n",
                },
            }
        ),
    )
    assert r.returncode == 2
    out = json.loads(r.stdout)["output"]
    assert "TRACE AUTHORING REQUIRED" in out
    assert "satisfies=REQ-repository-discovery" in out  # the active requirement, auto-attached
    assert "work=WORK-local-repository-dependency-scanner" in out


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_ambient_auto_init_on_hook type=test
def test_ambient_auto_init_on_hook(tmp_path):
    """A hook firing in a fresh git repo bootstraps TraceLayer silently."""
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    assert not (repo / ".trace" / "trace.toml").exists()
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "a.py"}),
    )
    assert r.returncode == 0, r.stderr
    assert (repo / ".trace" / "trace.toml").exists()  # auto-initialized
    assert (repo / "AGENTS.md").exists()  # invariant installed
    assert "tracelayer-agent-invariant:v2" in (repo / "AGENTS.md").read_text()


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_causal_gate_directs_automatic_bootstrap type=test
def test_causal_gate_directs_automatic_bootstrap(tmp_path):
    """No causal context -> AMBIENT TRACE BOOTSTRAP REQUIRED, no ID requests."""
    repo = _fresh_repo(tmp_path)
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "no-ctx"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "src" / "lonely.py"),
                    "content": "def wander():\n    return 1\n",
                },
            }
        ),
    )
    assert r.returncode == 2
    out = json.loads(r.stdout)["output"]
    assert "AMBIENT TRACE BOOTSTRAP REQUIRED" in out
    assert "trace task bootstrap --json" in out
    assert "Do not ask the user for TraceLayer IDs" in out


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_task_activate_resolves_all_requirements type=test
def test_task_activate_resolves_all_requirements(tmp_path):
    """trace task activate pulls every requirement for the work from the graph."""
    repo = _fresh_repo(tmp_path)
    result = json.loads(
        run_trace(
            repo, "task", "bootstrap", env={"TRACE_SESSION": "a"}, input=json.dumps(SCANNER_BUNDLE)
        ).stdout
    )
    run_trace(repo, "task", "end", env={"TRACE_SESSION": "a"})
    act = json.loads(
        run_trace(repo, "task", "activate", result["work"], env={"TRACE_SESSION": "a"}).stdout
    )
    assert len(act["requirements"]) == 4
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "a"}).stdout)
    assert len(ctx["requirements"]) == 4


ONE_REQ = {
    "title": "Configurable output threshold",
    "kind": "feature_extension",
    "intent": "Make the threshold configurable",
    "requirements": [
        {"title": "Configurable threshold", "statement": "Allow the threshold to be configured."},
    ],
}


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_marker_injection_via_updated_input type=test
def test_marker_injection_via_updated_input(tmp_path):
    """Ambient §19: single requirement -> Claude gets allow + updatedInput
    with the marker already injected (no second model mutation)."""
    repo = _fresh_repo(tmp_path)
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "inj"}, input=json.dumps(ONE_REQ))
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "claude",
        env={"TRACE_SESSION": "inj"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "src" / "threshold.py"),
                    "content": "def apply_threshold(value):\n    return value > 2\n",
                },
            }
        ),
    )
    assert r.returncode == 0
    hso = json.loads(r.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert "updatedInput" in hso
    content = hso["updatedInput"]["content"]
    assert "# trace:v1 id=impl.apply-threshold work=WORK-configurable-output-threshold" in content
    assert "satisfies=REQ-configurable-threshold" in content
    # the marker is attached directly above the def
    lines = content.split("\n")
    marker_idx = next(i for i, ln in enumerate(lines) if "trace:v1" in ln)
    assert lines[marker_idx + 1].startswith("def apply_threshold")


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_multiple_requirements_fall_back_to_block type=test
def test_multiple_requirements_fall_back_to_block(tmp_path):
    """Ambient §19: multiple active requirements -> semantic mapping is the
    agent's call, so deny with the authoring plan."""
    repo = _fresh_repo(tmp_path)
    run_trace(
        repo, "task", "bootstrap", env={"TRACE_SESSION": "inj"}, input=json.dumps(SCANNER_BUNDLE)
    )
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "claude",
        env={"TRACE_SESSION": "inj"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "src" / "scanner.py"),
                    "content": "def discover_repositories():\n    return []\n",
                },
            }
        ),
    )
    assert r.returncode == 0
    hso = json.loads(r.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "updatedInput" not in hso


# trace:v1 id=test.dogfood.tests.integration.test_ambient.test_json_mutation_falls_back_to_block type=test
def test_json_mutation_falls_back_to_block(tmp_path):
    """JSON cannot carry comments: sidecar path -> block, never rewrite."""
    repo = _fresh_repo(tmp_path)
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "inj"}, input=json.dumps(ONE_REQ))
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "claude",
        env={"TRACE_SESSION": "inj"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "config.json"),
                    "content": '{"server": {"port": 8080}}',
                },
            }
        ),
    )
    hso = json.loads(r.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
