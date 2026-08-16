"""Ambient intake: prose-only lifecycle and the intake state machine
(adversarial review v2 release gate: natural-language intake, pending
bootstrap, spec-evolution enforcement, and the black-box acceptance test).
"""

from __future__ import annotations

import json
import re

from tests.conftest import make_git_repo, run_trace

PROSE = (
    "Build a program that scans my computer for GitHub repos with "
    "node_modules over 2 GB and lists the biggest first."
)

BUNDLE = {
    "title": "Repository size scanner",
    "kind": "greenfield_project",
    "intent": "Find Git repositories with oversized node_modules directories",
    "requirements": [
        {
            "title": "Repository discovery",
            "statement": "JSON output is optional.",
        },
    ],
}


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake type=test
# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_prompt_hook_sets_pending_bootstrap type=test
def test_prompt_hook_sets_pending_bootstrap(tmp_path):
    """New prose with no causal context -> pending bootstrap, and the FIRST
    code mutation is gated on a semantic bootstrap (no `trace init`, no IDs)."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    assert not (repo / ".trace" / "trace.toml").exists()
    r = run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"prompt": PROSE}),
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["intake"] == "needs_bootstrap"
    assert (repo / ".trace" / "trace.toml").exists()  # auto-initialized, no init ceremony
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "s"}).stdout)
    assert ctx["pending_bootstrap"]
    # the first code mutation is blocked with the semantic-bootstrap instruction
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
                    "content": "def find_large_repos():\n    return []\n",
                },
            }
        ),
    )
    assert r.returncode == 2
    out = json.loads(r.stdout)["output"]
    assert "NEW INTENT, NO CAUSAL CONTEXT" in out
    assert "trace task bootstrap --prompt" in out
    assert "Do not ask the user" not in out  # the pending-bootstrap path, not the generic one


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_prompt_hook_auto_activates_existing_work type=test
def test_prompt_hook_auto_activates_existing_work(tmp_path):
    """A strong resolve match in a fresh session activates work + requirements
    + plan automatically at UserPromptSubmit time."""
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "creator"},
        input=json.dumps(
            {
                "title": "Local repository dependency scanner",
                "kind": "greenfield_project",
                "intent": "Find Git repositories with oversized node_modules",
                "requirements": [
                    {"title": "Repository discovery", "statement": "Discover repositories."}
                ],
                "plan": {"recommended": True, "steps": ["discover"]},
            }
        ),
    )
    r = run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env={"TRACE_SESSION": "other"},
        input=json.dumps({"prompt": "continue the scanner"}),
    )
    out = json.loads(r.stdout)
    assert out["intake"] == "resumed"
    assert out["active_work"] == "WORK-local-repository-dependency-scanner"
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "other"}).stdout)
    assert ctx["work"] == "WORK-local-repository-dependency-scanner"
    assert ctx["requirements"] == ["REQ-repository-discovery"]
    assert ctx["plan"] == "PLAN-local-repository-dependency-scanner"
    assert not ctx["pending_bootstrap"]


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_from_prompt_mints_everything type=test
def test_bootstrap_from_prompt_mints_everything(tmp_path):
    """trace task bootstrap --prompt derives the whole causal chain from the
    user's prose alone: work + spec + requirement + plan, no IDs supplied."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        "--prompt",
        PROSE,
        env={"TRACE_SESSION": "s"},
    )
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["work"].startswith("WORK-")
    assert len(result["requirements"]) == 1
    assert result["plan"].startswith("PLAN-")
    assert result["requirements"][0]["id"].startswith("REQ-")
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert result["requirements"][0]["id"] in spec
    assert (repo / ".trace" / "work.toml").exists()
    assert result["work"] in (repo / ".trace" / "work.toml").read_text(encoding="utf-8")


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_rejects_behavioral_bundle_without_requirements type=test
def test_bootstrap_rejects_behavioral_bundle_without_requirements(tmp_path):
    """A behavioral task kind with zero requirements must be rejected — new
    product behavior always gets requirements/spec before implementation."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    for kind in (
        "greenfield_project",
        "new_feature",
        "behavior_change",
        "bug_contract_backfill",
    ):
        r = run_trace(
            repo,
            "task",
            "bootstrap",
            env={"TRACE_SESSION": "s"},
            input=json.dumps({"title": "New product", "kind": kind, "requirements": []}),
        )
        assert r.returncode != 0, kind
        assert "at least one requirement" in r.stderr, kind
    # refactor-like kinds may legitimately add zero requirements
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "title": "Rename internals",
                "kind": "refactor",
                "intent": "Internal renaming only",
                "requirements": [],
            }
        ),
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["work"].startswith("WORK-")


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_slug_collision_in_one_transaction type=test
def test_bootstrap_slug_collision_in_one_transaction(tmp_path):
    """Two titles that slug identically get distinct REQ ids in ONE bootstrap
    (transaction-local allocator, not just store lookups)."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "title": "Rate limits",
                "kind": "new_feature",
                "intent": "limit",
                "requirements": [
                    {"title": "API Rate Limit", "statement": "a"},
                    {"title": "API-Rate-Limit", "statement": "b"},
                ],
            }
        ),
    )
    assert r.returncode == 0, r.stderr
    reqs = json.loads(r.stdout)["requirements"]
    assert len({q["id"] for q in reqs}) == 2  # both slug to REQ-api-rate-limit; second gets -2


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_intake_behavior_change_gates_implementation type=test
def test_intake_behavior_change_gates_implementation(tmp_path):
    """BEHAVIOR_CHANGE intake blocks implementation edits until the governing
    requirement's text actually changes (spec evolution is enforced, not
    voluntary)."""
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    boot = json.loads(
        run_trace(
            repo,
            "task",
            "bootstrap",
            env={"TRACE_SESSION": "s"},
            input=json.dumps(BUNDLE),
        ).stdout
    )
    work_id = boot["work"]
    req_id = boot["requirements"][0]["id"]
    # the user now says: make JSON output the default. The agent classifies.
    r = run_trace(
        repo,
        "task",
        "intake",
        "--kind",
        "behavior-change",
        work_id,
        "--requirements",
        req_id,
        env={"TRACE_SESSION": "s"},
    )
    assert r.returncode == 0, r.stderr
    intake = json.loads(r.stdout)
    assert intake["state"]["pending_spec_update"] == [req_id]
    # implementation edit is blocked until the spec changes
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
                    "file_path": str(repo / "src" / "output.py"),
                    "content": "def render():\n    return 'json'\n",
                },
            }
        ),
    )
    assert r.returncode == 2
    out = json.loads(r.stdout)["output"]
    assert "SPEC UPDATE REQUIRED" in out
    assert req_id in out
    # the requirement text changes (the spec edit itself is allowed)
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    new_spec = spec.replace("JSON output is optional.", "JSON output is the default.")
    assert new_spec != spec
    (repo / "docs" / "spec.md").write_text(new_spec, encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "docs/spec.md"}),
    )
    assert r.returncode == 0, r.stderr
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "s"}).stdout)
    assert ctx["pending_spec_update"] == []  # fingerprint changed: gate cleared


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_edit_denied_with_plan_not_rewritten type=test
def test_edit_denied_with_plan_not_rewritten(tmp_path):
    """Automatic updatedInput rewriting is WRITE-only: an Edit must never be
    rewritten as a whole-file replacement (adversarial review P0)."""
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(BUNDLE))
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "existing.py").write_text("def existing():\n    return 0\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "claude",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(repo / "src" / "existing.py"),
                    "old_string": "return 0",
                    "new_string": "def new_feature():\n    return 2\n",
                },
            }
        ),
    )
    hso = json.loads(r.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "updatedInput" not in hso  # never a whole-file rewrite in an Edit
    assert "TRACE AUTHORING REQUIRED" in hso["permissionDecisionReason"]


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_black_box_prose_only_lifecycle type=test
def test_black_box_prose_only_lifecycle(tmp_path):
    """THE acceptance test: the ONLY semantic input is the user's prose.

    No `trace init`, no bundle constructed in test code, no TraceLayer ID
    anywhere in the input. The harness must auto-create .trace, WORK, spec,
    REQs, plan, attach local traces, trace the test, ingest evidence, and
    finalize the work — end to end.
    """
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    assert not re.search(r"\b(WORK|REQ|PLAN)-[A-Za-z0-9]", PROSE)  # the input has NO IDs

    # 1. user prose arrives at UserPromptSubmit
    r = run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"prompt": PROSE}),
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["intake"] == "needs_bootstrap"
    assert (repo / ".trace" / "trace.toml").exists()  # .trace auto-created
    assert (repo / "AGENTS.md").exists()

    # 2. the agent bootstraps from the prose alone (no bundle authored by hand)
    r = run_trace(repo, "task", "bootstrap", "--prompt", PROSE, env={"TRACE_SESSION": "s"})
    assert r.returncode == 0, r.stderr
    boot = json.loads(r.stdout)
    work_id, req_id = boot["work"], boot["requirements"][0]["id"]
    assert boot["plan"]  # plan auto-created where appropriate
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert req_id in spec and work_id in spec  # spec auto-created with REQ markers
    assert (repo / ".trace" / "work.toml").read_text(encoding="utf-8").count("WORK-") >= 1

    # 3. first code Write: the authoring gate injects the marker (single REQ)
    r = run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "claude",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(repo / "scanner.py"),
                    "content": "def find_large_repos():\n    return []\n",
                },
            }
        ),
    )
    hso = json.loads(r.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    injected = hso["updatedInput"]["content"]
    assert "trace:v1" in injected
    m = re.search(r"id=([A-Za-z0-9._:/-]+)", injected)
    assert m, injected
    impl_id = m.group(1)
    assert impl_id.startswith("impl.")
    (repo / "scanner.py").write_text(injected, encoding="utf-8")  # apply the injected write
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "scanner.py"}),
    )
    assert r.returncode == 0, r.stderr

    # 4. verification test, traced with verifies= + exercises=
    (repo / "test_scanner.py").write_text(
        f"# trace:v1 id=TEST-scanner verifies={req_id} exercises={impl_id}\n"
        "def test_find():\n"
        "    assert find_large_repos() == []\n",
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"path": "test_scanner.py"}),
    )
    assert r.returncode == 0, r.stderr

    # 5. evidence (inside .trace/ so it never pollutes the changed scope)
    import subprocess

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    ev = repo / ".trace" / "evidence.json"
    ev.write_text(
        json.dumps(
            {
                "schema": "tracelayer-evidence/v1",
                "run_id": "local-run",
                "status": "pass",
                "revision": head,
                "tests": [
                    {"framework_id": "TEST-scanner", "outcome": "pass", "trace_id": "TEST-scanner"}
                ],
                "execution_edges": [
                    {
                        "test_uid": "TEST-scanner",
                        "implementation_uid": impl_id,
                        "coverage_kind": "per_test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "evidence",
        "ingest",
        "--normalized",
        str(ev),
        "--revision",
        head,
        env={"TRACE_SESSION": "s"},
    )
    assert r.returncode == 0, r.stderr

    # 6. finalize: merge-grade gate passes -> work done, session cleared
    r = run_trace(repo, "task", "finish", env={"TRACE_SESSION": "s"})
    assert r.returncode == 0, r.stdout + r.stderr
    fin = json.loads(r.stdout)
    assert fin["status"] == "done", fin
    assert 'status = "done"' in (repo / ".trace" / "work.toml").read_text(encoding="utf-8")
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "s"}).stdout)
    assert ctx["work"] is None  # session cleared

    # 7. evidence + receipts exist; receipts are bound to the commit
    receipts = [
        json.loads(ln)
        for ln in (repo / ".trace" / "receipts" / "receipts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln.strip()
    ]
    assert receipts
    assert all(rec.get("commit") == head for rec in receipts)  # bound to the final commit
    status = json.loads(run_trace(repo, "status", "--json").stdout)
    assert status["broken_refs"] == 0
    # the whole causal chain exists: work, requirement, plan, impl, test
    v = run_trace(repo, "verify", "--all", "--lifecycle", "merge")
    assert v.returncode == 0, v.stdout


# ---------------------------------------------------------------------------
# Adversarial review v3 (self-audit) regressions
# ---------------------------------------------------------------------------


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bash_scan_respects_policy_exclusions type=test
def test_bash_scan_respects_policy_exclusions(tmp_path):
    """A path the verify gate excludes (policy.toml [exclusions]) must never
    get a Bash-scan obligation — gate and hooks classify identically."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    pol = (repo / ".trace" / "policy.toml").read_text(encoding="utf-8")
    pol = pol.replace('"coverage.xml"', '"coverage.xml",\n  "pyproject.toml"')
    (repo / ".trace" / "policy.toml").write_text(pol, encoding="utf-8")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(BUNDLE))
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "touch pyproject.toml"}}),
    )
    out = json.loads(r.stdout)
    assert not any("pyproject.toml" in o for o in out.get("created_obligations", []))
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "s"}).stdout)
    assert ctx["pending_obligations"] == []


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bash_authored_marker_resolves_obligation type=test
def test_bash_authored_marker_resolves_obligation(tmp_path):
    """A marker authored via Bash (sed) resolves the scan-created obligation
    on the NEXT scan — the stop gate is not stuck forever."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(BUNDLE))
    (repo / "scanner.py").write_text("def discover():\n    return []\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat > scanner.py"}}),
    )
    assert json.loads(r.stdout).get("created_obligations") == ["scanner.py::discover"]
    (repo / "scanner.py").write_text(
        "# \x74race:v1 id=impl.scanner.discover work=WORK-repository-size-scanner "
        "satisfies=REQ-repository-discovery\n"
        "def discover():\n    return []\n",
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "sed -i"}}),
    )
    assert r.returncode == 0, r.stderr
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "s"}).stdout)
    assert ctx["pending_obligations"] == []
    r = run_trace(
        repo,
        "hook",
        "stop",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"lifecycle": "wip"}),
    )
    assert r.returncode == 0, r.stdout  # obligations resolved, stop not stuck


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_rejects_duplicate_requirement_titles type=test
def test_bootstrap_rejects_duplicate_requirement_titles(tmp_path):
    """Identical requirement titles would collapse to one REQ id — rejected."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "title": "Limits",
                "kind": "new_feature",
                "intent": "limit",
                "requirements": [
                    {"title": "API Rate Limit", "statement": "a"},
                    {"title": "API Rate Limit", "statement": "b"},
                ],
            }
        ),
    )
    assert r.returncode != 0
    assert "must be unique" in r.stderr


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_rejects_non_list_plan_steps type=test
def test_bootstrap_rejects_non_list_plan_steps(tmp_path):
    """plan.steps must be a list of strings — a bare string would render
    one step per character."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(
            {
                "title": "Limits",
                "kind": "new_feature",
                "intent": "limit",
                "requirements": [{"title": "Rate", "statement": "a"}],
                "plan": {"recommended": True, "steps": "P1 rate"},
            }
        ),
    )
    assert r.returncode != 0
    assert "plan.steps" in r.stderr


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_referential_prompt_with_stopwords_resumes type=test
def test_referential_prompt_with_stopwords_resumes(tmp_path):
    """ "fix it" under an active session resumes — the demonstrative check
    uses word boundaries, not bare substring matching."""
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(BUNDLE),
    )
    for prompt in ("fix it", "do that now", "make this work"):
        r = run_trace(
            repo,
            "task",
            "resolve",
            "--prompt",
            prompt,
            env={"TRACE_SESSION": "s"},
        )
        assert json.loads(r.stdout)["resolution"] == "resume", prompt


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_activate_clears_pending_bootstrap type=test
def test_activate_clears_pending_bootstrap(tmp_path):
    """A resumed work has causal context: activation clears a stale
    pending-bootstrap marker."""
    repo = make_git_repo(tmp_path, {"README.md": "# scanner\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(
        repo,
        "task",
        "bootstrap",
        env={"TRACE_SESSION": "creator"},
        input=json.dumps(BUNDLE),
    )
    # new intent in a fresh session -> pending bootstrap
    run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env={"TRACE_SESSION": "other"},
        input=json.dumps({"prompt": PROSE}),
    )
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "other"}).stdout)
    assert ctx["pending_bootstrap"]
    # then the user says "continue the scanner" -> resolve + activate clears it
    run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env={"TRACE_SESSION": "other"},
        input=json.dumps({"prompt": "continue the scanner"}),
    )
    ctx = json.loads(run_trace(repo, "task", "context", env={"TRACE_SESSION": "other"}).stdout)
    assert ctx["work"] == "WORK-repository-size-scanner"
    assert not ctx["pending_bootstrap"]


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_finish_idle_without_active_work type=test
def test_finish_idle_without_active_work(tmp_path):
    """finish with no active task reports idle — never a misleading 'done'."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(repo, "task", "finish", env={"TRACE_SESSION": "nobody"})
    assert json.loads(r.stdout)["status"] == "idle"


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bootstrap_json_flag_and_empty_stdin type=test
def test_bootstrap_json_flag_and_empty_stdin(tmp_path):
    """The documented `--json` form must work, and empty stdin must fail
    with a helpful message — not a cryptic JSON traceback."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    r = run_trace(
        repo,
        "task",
        "bootstrap",
        "--json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps(BUNDLE),
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["work"].startswith("WORK-")
    # empty stdin -> actionable guidance, never "Expecting value: line 1"
    r = run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"})
    assert r.returncode == 2
    assert "--prompt" in r.stderr
    assert "pipe a JSON bundle on stdin" in r.stderr
    assert "Expecting value" not in r.stderr
    # the --json form names the documented pipe exactly
    r = run_trace(repo, "task", "bootstrap", "--json", env={"TRACE_SESSION": "s"})
    assert r.returncode == 2
    assert "bundle.json" in r.stderr


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_init_policy_excludes_agent_dirs type=test
def test_init_policy_excludes_agent_dirs(tmp_path):
    """Fresh-init policy excludes agent install dirs so the installed skill
    copy can never create spurious Bash-scan obligations."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", env={"HOME": str(tmp_path / "home")})
    pol = (repo / ".trace" / "policy.toml").read_text(encoding="utf-8")
    for entry in (
        ".agents/**",
        ".claude/**",
        ".codex/**",
        ".pi/**",
        ".omp/**",
        ".hermes/**",
        "opencode.json",
    ):
        assert entry in pol, entry
    run_trace(repo, "install", "--agent", "generic", "--yes", env={"HOME": str(tmp_path / "home")})
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(BUNDLE))
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    )
    out = json.loads(r.stdout)
    assert not any(".agents" in o for o in out.get("created_obligations", []))
    assert not any(
        ".claude" in o or ".omp" in o or ".pi" in o for o in out.get("created_obligations", [])
    )


# trace:v1 id=test.dogfood.tests.integration.test_ambient_intake.test_bash_scan_distinguishes_new_vs_pending type=test
def test_bash_scan_distinguishes_new_vs_pending(tmp_path):
    """The Bash scan must report NEW obligations vs already-pending ones
    honestly — re-listing existing pendings as 'created' sent agents into
    re-doing resolved work (system_ir transcript)."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "task", "bootstrap", env={"TRACE_SESSION": "s"}, input=json.dumps(BUNDLE))
    (repo / "mcp.rs").write_text("fn send() {}\nfn reply() {}\nfn error() {}\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat > mcp.rs"}}),
    )
    o1 = json.loads(r.stdout)
    assert len(o1.get("created_obligations", [])) == 3
    # no new mutations: re-scan must NOT claim new creations
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "touch mcp.rs"}}),
    )
    o2 = json.loads(r.stdout)
    assert o2.get("created_obligations") == []
    assert len(o2.get("pending_obligations", [])) == 3
    output = o2.get("output", "")
    assert "NEW TRACE OBLIGATION" not in output
    assert "already pending" in output
    # marker authored via Bash resolves: the next scan lists only the rest
    (repo / "mcp.rs").write_text(
        "# \x74race:v1 id=impl.mcp.send work=WORK-repository-size-scanner "
        "satisfies=REQ-repository-discovery\n"
        "fn send() {}\nfn reply() {}\nfn error() {}\n",
        encoding="utf-8",
    )
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "sed -i"}}),
    )
    o3 = json.loads(r.stdout)
    assert "send" not in str(o3.get("pending_obligations", []))
    assert "reply" in str(o3.get("pending_obligations", []))
