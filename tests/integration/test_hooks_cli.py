"""Hook CLI tests (spec Section 22): pre-mutation block-once semantics,
TRACE_SESSION session binding, the stop gate, and prompt-context injection.

Payloads are passed on stdin exactly as the generic-json hook adapters do:
``{"event": ..., "payload": {...}, "session_id": ...}`` (the envelope is
optional; raw payload dicts work too).  A blocking decision exits 1.
"""

from __future__ import annotations

import json

from tests.integration._fixtures import (
    IMPL_BODY_LINE,
    change_requirement,
    run_trace,
    setup_auth_repo,
)

SESSION = "sess-e2e"
PRE_MUTATION = json.dumps(
    {
        "payload": {
            "path": "src/auth/tokens.py",
            "line": IMPL_BODY_LINE,
            "session_id": SESSION,
        }
    }
)


def test_pre_mutation_blocks_once_then_allows(tmp_path):
    """First context-free edit of protected behavior blocks; the retry is
    allowed (pre_edit_block_once) without loading context."""
    root = setup_auth_repo(tmp_path)

    blocked = run_trace(root, "hook", "pre-mutation", input=PRE_MUTATION)
    assert blocked.returncode == 2
    assert "TRACE CONTEXT REQUIRED" in blocked.stdout
    assert "impl.auth.refresh" in blocked.stdout
    assert "Run `trace context impl.auth.refresh`" in blocked.stdout
    assert "REQ-AUTH-017" in blocked.stdout
    assert "WORK-AUTH-237" in blocked.stdout
    assert "test.auth.refresh-reuse" in blocked.stdout

    retry = run_trace(root, "hook", "pre-mutation", input=PRE_MUTATION)
    assert retry.returncode == 0
    assert retry.stdout.strip() == ""


def test_pre_mutation_allows_after_context_load(tmp_path):
    """After `trace context <id>` runs in the same session, the edit is
    allowed immediately (context acknowledgement is stored per session)."""
    root = setup_auth_repo(tmp_path)
    env = {"TRACE_SESSION": SESSION}

    blocked = run_trace(root, "hook", "pre-mutation", input=PRE_MUTATION, env=env)
    assert blocked.returncode == 2

    # The context command records the load under the same session id.
    ctx = run_trace(root, "context", "impl.auth.refresh", env=env)
    assert ctx.returncode == 0

    allowed = run_trace(root, "hook", "pre-mutation", input=PRE_MUTATION, env=env)
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == ""


def test_pre_mutation_sessions_are_isolated(tmp_path):
    """Session state is keyed by session id: blocking in one session does
    not leak into another, and the env var binds the default session."""
    root = setup_auth_repo(tmp_path)
    payload_a = json.dumps(
        {
            "payload": {
                "path": "src/auth/tokens.py",
                "line": IMPL_BODY_LINE,
                "session_id": "alice",
            }
        }
    )
    payload_b = json.dumps(
        {
            "payload": {
                "path": "src/auth/tokens.py",
                "line": IMPL_BODY_LINE,
                "session_id": "bob",
            }
        }
    )

    # Alice's first edit is blocked, her retry is allowed (block-once).
    assert run_trace(root, "hook", "pre-mutation", input=payload_a).returncode == 2
    assert run_trace(root, "hook", "pre-mutation", input=payload_a).returncode == 0

    # Bob is a fresh session: still blocked despite Alice's state.
    assert run_trace(root, "hook", "pre-mutation", input=payload_b).returncode == 2

    # A session bound via TRACE_SESSION behaves like a distinct session id.
    env_blocked = run_trace(
        root, "hook", "pre-mutation", input=PRE_MUTATION, env={"TRACE_SESSION": "env-session"}
    )
    assert env_blocked.returncode == 2
    # Bob's block-once state is untouched by the env session.
    assert run_trace(root, "hook", "pre-mutation", input=payload_b).returncode == 0


def test_stop_gate_blocks_on_dirty_evidence_then_passes(tmp_path):
    """Stop hook fails closed with un-reviewed stale traces at merge, then
    passes once the requirement is reviewed (spec 22.9)."""
    root = setup_auth_repo(tmp_path)

    # Clean baseline: stop at wip passes (no evidence gates at wip).
    clean = run_trace(root, "hook", "stop", input=json.dumps({"payload": {"lifecycle": "wip"}}))
    assert clean.returncode == 0
    assert "Trace verify passed under lifecycle wip." in clean.stdout

    # Requirement change -> stale downstream -> stop at merge blocks.
    change_requirement(root)
    assert run_trace(root, "index", "--changed").returncode == 0

    blocked = run_trace(root, "hook", "stop", input=json.dumps({"payload": {"lifecycle": "merge"}}))
    assert blocked.returncode == 2
    assert "Task cannot complete yet." in blocked.stdout
    assert "[TL011]" in blocked.stdout  # changed requirement -> stale downstream
    assert "trace review" in blocked.stdout

    # Review the changed requirement; the gate now passes (wip gates are off).
    assert run_trace(root, "review", "REQ-AUTH-017").returncode == 0
    passed = run_trace(root, "hook", "stop", input=json.dumps({"payload": {"lifecycle": "wip"}}))
    assert passed.returncode == 0
    assert "Trace verify passed under lifecycle wip." in passed.stdout


def test_stop_gate_json_envelope(tmp_path):
    """The stop hook JSON envelope reports the decision, status, and
    lifecycle deterministically."""
    root = setup_auth_repo(tmp_path)
    proc = run_trace(
        root,
        "hook",
        "stop",
        "--format",
        "json",
        input=json.dumps({"payload": {"lifecycle": "wip"}}),
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["event"] == "stop"
    assert data["decision"] == "allow"
    assert data["status"] == "pass"
    assert data["lifecycle"] == "wip"
    # Only non-blocking configuration-change warnings (TL063) may appear.
    assert all(d["rule"] == "TL063" for d in data["diagnostics"])


def test_prompt_context_injects_nothing_on_no_hits(tmp_path):
    """A prompt with no trace hits injects nothing (spec 22.2)."""
    root = setup_auth_repo(tmp_path)
    proc = run_trace(
        root,
        "hook",
        "prompt-context",
        input=json.dumps({"payload": {"prompt": "zzzz unrelated gibberish"}}),
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_hook_unknown_event_exits_2(tmp_path):
    """An unknown hook event is a usage error (exit 2), not a block."""
    root = setup_auth_repo(tmp_path)
    proc = run_trace(root, "hook", "bogus-event", input="{}")
    assert proc.returncode == 2
    assert "unknown hook event" in proc.stderr or "unknown hook event" in proc.stdout
