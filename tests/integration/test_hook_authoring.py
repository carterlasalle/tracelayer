"""Authoring gate + obligations + adapter contract integration tests (review P0)."""

from __future__ import annotations

import json
import subprocess

from tests.conftest import make_git_repo, run_trace

REQ_FILES = {
    "req.md": "## REQ-AUTH-017 - Rotation\n\n<!-- \x74race:v1 id=REQ-AUTH-017 type=requirement work=WORK-AUTH-237 -->\n",
    "src/auth.py": "# \x74race:v1 id=impl.auth.rotate work=WORK-AUTH-237 satisfies=REQ-AUTH-017\ndef rotate(t):\n    return f'rotated-{t}'\n",
}


# trace:v1 id=test.dogfood.tests.integration.test_hook_authoring.py type=test
def _repo(tmp_path):
    repo = make_git_repo(tmp_path, REQ_FILES)
    (repo / ".trace").mkdir(parents=True)
    (repo / ".trace" / "work.toml").write_text(
        '[work."WORK-AUTH-237"]\ntitle = "Refresh token rotation"\n',
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    return repo


def _pre(repo, payload: dict, session: str = "s") -> subprocess.CompletedProcess:
    return run_trace(
        repo,
        "hook",
        "pre-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": session},
        input=json.dumps(payload),
    )


def _post(repo, payload: dict, session: str = "s") -> dict:
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env={"TRACE_SESSION": session},
        input=json.dumps(payload),
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_claude_payload_normalized_and_authoring_blocked(tmp_path):
    """Claude's tool_input shape reaches the authoring gate (adapter fix)."""
    repo = _repo(tmp_path)
    r = run_trace(
        repo,
        "task",
        "begin",
        "WORK-AUTH-237",
        "--requirement",
        "REQ-AUTH-017",
        env={"TRACE_SESSION": "claude-s"},
    )
    assert r.returncode == 0, r.stderr
    claude_payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(repo / "src" / "auth.py"),
            "old_string": "def rotate(t):\n    return f'rotated-{t}'\n",
            "new_string": "def rotate(t):\n    return f'rotated-{t}'\n\n\ndef refresh(t):\n    return rotate(t)\n",
        },
    }
    r = _pre(repo, claude_payload, "claude-s")
    assert r.returncode == 2  # exit 2 = Claude's blocking exit code
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert out["new_symbol"] == "refresh"
    assert "work=WORK-AUTH-237" in out["output"]
    assert "satisfies=REQ-AUTH-017" in out["output"]


# trace:v1 id=test.dogfood.tests.integration.test_hook_authoring.authoring-gate type=test
def test_authoring_gate_blocks_and_exemption_allows(tmp_path):
    repo = _repo(tmp_path)
    run_trace(repo, "task", "begin", "WORK-AUTH-237", env={"TRACE_SESSION": "s"})
    # Write with a new function: blocked, obligation persisted
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "src" / "billing.py"),
            "content": "def create_invoice(total):\n    return total\n",
        },
    }
    r = _pre(repo, payload)
    assert r.returncode == 2
    assert json.loads(r.stdout)["new_symbol"] == "create_invoice"
    # Retry with the marker: allowed
    with_marker = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "src" / "billing.py"),
            "content": (
                "# \x74race:v1 id=impl.billing.invoice work=WORK-AUTH-237 satisfies=REQ-AUTH-017\n"
                "def create_invoice(total):\n    return total\n"
            ),
        },
    }
    r = _pre(repo, with_marker)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "allow"
    # Exemption comment allows without a marker
    exempt = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "src" / "trivial.py"),
            "content": "# trace:exempt reason=trivial-helper\ndef helper():\n    return 0\n",
        },
    }
    r = _pre(repo, exempt)
    assert r.returncode == 0, r.stderr


# trace:v1 id=test.dogfood.tests.integration.test_hook_authoring.modified-block type=test
def test_modified_untraced_boundary_blocks_before_edit(tmp_path):
    """Rewriting an existing untraced function is blocked pre-edit too."""
    repo = _repo(tmp_path)
    run_trace(repo, "task", "begin", "WORK-AUTH-237", env={"TRACE_SESSION": "s"})
    (repo / "src" / "legacy.py").write_text(
        "def legacy_payment_flow():\n    return old_logic()\n", encoding="utf-8"
    )
    r = _pre(
        repo,
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(repo / "src" / "legacy.py"),
                "old_string": "def legacy_payment_flow():\n    return old_logic()\n",
                "new_string": "def legacy_payment_flow():\n    return completely_new_logic()\n",
            },
        },
    )
    assert r.returncode == 2, r.stderr
    out = json.loads(r.stdout)["output"]
    assert "TRACE AUTHORING REQUIRED" in out
    assert "legacy_payment_flow" in out
    assert "modified untraced" in out


# trace:v1 id=test.dogfood.tests.integration.test_hook_authoring.no-causal type=test
def test_no_causal_context_blocks_with_task_begin_hint(tmp_path):
    repo = _repo(tmp_path)  # session has no active work/requirement
    r = _pre(
        repo,
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(repo / "src" / "lonely.py"),
                "content": "def wander():\n    return 1\n",
            },
        },
    )
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert "AMBIENT TRACE BOOTSTRAP REQUIRED" in out["output"]
    assert "trace task bootstrap" in out["output"]
    assert "Do not ask the user for TraceLayer IDs" in out["output"]


# trace:v1 id=test.dogfood.tests.integration.test_hook_authoring.obligation-stop type=test
def test_obligation_blocks_stop_until_resolved(tmp_path):
    repo = _repo(tmp_path)
    run_trace(repo, "task", "begin", "WORK-AUTH-237", env={"TRACE_SESSION": "s"})
    blocked = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "src" / "payments.py"),
            "content": "def charge():\n    return 1\n",
        },
    }
    r = _pre(repo, blocked)
    assert r.returncode == 2
    # Stop blocks while the obligation is pending
    r = run_trace(
        repo,
        "hook",
        "stop",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"lifecycle": "wip"}),
    )
    assert r.returncode == 2, r.stdout
    out = json.loads(r.stdout)
    assert "TRACE OBLIGATIONS PENDING" in out["output"]
    assert "payments.py::src.payments.charge" in out["output"]
    # The agent retries with the marker; post-mutation resolves it
    (repo / "src" / "payments.py").write_text(
        "# \x74race:v1 id=impl.payments.charge work=WORK-AUTH-237 satisfies=REQ-AUTH-017\n"
        "def charge():\n    return 1\n",
        encoding="utf-8",
    )
    _post(repo, {"path": "src/payments.py"})
    r = run_trace(
        repo,
        "hook",
        "stop",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"lifecycle": "wip"}),
    )
    assert r.returncode == 0, r.stdout
    assert "TRACE OBLIGATIONS PENDING" not in json.loads(r.stdout)["output"]


def test_marker_suggest_with_session_context(tmp_path):
    repo = _repo(tmp_path)
    run_trace(
        repo,
        "task",
        "begin",
        "WORK-AUTH-237",
        "--requirement",
        "REQ-AUTH-017",
        env={"TRACE_SESSION": "s"},
    )
    (repo / "src" / "billing.py").write_text("def create_invoice(total):\n    return total\n")
    r = run_trace(
        repo,
        "marker",
        "suggest",
        "src/billing.py:1",
        env={"TRACE_SESSION": "s"},
    )
    assert r.returncode == 0, r.stderr
    assert "create_invoice" in r.stdout
    assert (
        "# \x74race:v1 id=impl.create_invoice work=WORK-AUTH-237 satisfies=REQ-AUTH-017" in r.stdout
    )


def test_stop_blocks_untraced_new_file_via_changed_scope(tmp_path):
    """Stop now evaluates changed scope: a brand-new untraced file blocks."""
    repo = _repo(tmp_path)
    (repo / "src" / "untraced.py").write_text("def sneaky():\n    return 1\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "stop",
        "--format",
        "json",
        env={"TRACE_SESSION": "s"},
        input=json.dumps({"lifecycle": "wip"}),
    )
    assert r.returncode == 2, r.stdout
    out = json.loads(r.stdout)
    assert "TL012" in out["output"]
