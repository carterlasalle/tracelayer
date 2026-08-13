"""End-to-end CLI tests: the spec Section 50 authentication feature flow and
the ``trace verify`` exit-code contract (spec 28.6).

Everything runs through the real ``trace`` binary via the shared
``run_trace`` helper against freshly built git repos in ``tmp_path``.
"""

from __future__ import annotations

import json
import re

from tests.integration._fixtures import (
    change_requirement,
    head_revision,
    ingest_pass_evidence,
    run_trace,
    setup_auth_repo,
)


def test_auth_feature_end_to_end(tmp_path):
    """Spec 50 full flow: init -> index -> queries -> requirement change ->
    stale at merge -> review -> evidence ingest -> pass -> PR report."""
    root = setup_auth_repo(tmp_path)

    # --- baseline queries -------------------------------------------------
    status = run_trace(root, "status")
    assert status.returncode == 0
    assert "Nodes:" in status.stdout and "Policy: standard" in status.stdout

    status_json = json.loads(run_trace(root, "status", "--json").stdout)
    assert status_json["nodes"] == 7
    assert status_json["declared_edges"] == 9
    assert status_json["broken_refs"] == 0
    assert status_json["blocking_stale"] == 0

    ctx = run_trace(root, "context", "impl.auth.refresh")
    assert ctx.returncode == 0
    assert "impl.auth.refresh" in ctx.stdout
    assert "src/auth/tokens.py" in ctx.stdout
    assert "WORK-AUTH-237" in ctx.stdout
    assert "REQ-AUTH-017" in ctx.stdout

    why = run_trace(root, "why", "impl.auth.refresh")
    assert why.returncode == 0
    assert "REQ-AUTH-017" in why.stdout and "impl.auth.refresh" in why.stdout

    graph = run_trace(root, "graph", "REQ-AUTH-017")
    assert graph.returncode == 0
    assert "satisfies: impl.auth.refresh" in graph.stdout
    assert "verifies: test.auth.refresh-reuse" in graph.stdout

    impact = run_trace(root, "impact", "REQ-AUTH-017")
    assert impact.returncode == 0
    assert "impl.auth.refresh" in impact.stdout
    assert "test.auth.refresh-reuse" in impact.stdout

    search = run_trace(root, "search", "refresh")
    assert search.returncode == 0
    assert "REQ-AUTH-017" in search.stdout
    assert "impl.auth.refresh" in search.stdout

    new_id = run_trace(root, "new", "requirement", "--name", "session revocation")
    assert new_id.returncode == 0
    assert re.match(r"^REQ-[A-Za-z0-9-]+$", new_id.stdout.strip())

    # --- baseline verification passes at wip ------------------------------
    verify = run_trace(root, "verify", "--all")
    assert verify.returncode == 0
    assert "verify: pass" in verify.stdout

    # --- requirement change -> stale -> merge blocks ----------------------
    change_requirement(root)
    indexed = run_trace(root, "index", "--changed")
    assert indexed.returncode == 0

    stale_status = json.loads(run_trace(root, "status", "--json").stdout)
    assert stale_status["blocking_stale"] == 3
    assert stale_status["changed_artifacts"] == 4

    blocked = run_trace(root, "verify", "--all", "--lifecycle", "merge")
    assert blocked.returncode == 1
    assert "TL110" in blocked.stdout
    assert "TL011" in blocked.stdout
    assert "verify: FAIL" in blocked.stdout

    # --- review clears the stale state but evidence is still missing ------
    reviewed = run_trace(root, "review", "REQ-AUTH-017")
    assert reviewed.returncode == 0
    assert "REVIEWED_NEEDS_VERIFICATION" in reviewed.stdout

    after_review = run_trace(root, "verify", "--all", "--lifecycle", "merge", "--json")
    assert after_review.returncode == 1
    diags = json.loads(after_review.stdout)["diagnostics"]
    rules = {d["rule"] for d in diags}
    assert "TL110" not in rules  # stale state reviewed away
    assert "TL021" in rules  # linked test has no passing evidence yet

    # --- evidence ingest (junit + cobertura) -> merge passes --------------
    revision = head_revision(root)
    ingest_pass_evidence(root, tmp_path, revision)

    final = run_trace(root, "verify", "--all", "--lifecycle", "merge", "--json")
    assert final.returncode == 0
    payload = json.loads(final.stdout)
    assert payload["schema"] == "tracelayer-verify/v1"
    assert payload["status"] == "pass"
    assert payload["diagnostics"] == []

    # --- PR report carries the trace impact section -----------------------
    pr = run_trace(root, "report", "pr")
    assert pr.returncode == 0
    assert "## Trace Impact" in pr.stdout
    assert "**Work**" in pr.stdout
    assert "- WORK-AUTH-237" in pr.stdout
    assert "REQ-AUTH-017 - modified" in pr.stdout
    assert "`impl.auth.refresh`" in pr.stdout
    assert "`test.auth.refresh-reuse`" in pr.stdout


def test_verify_before_any_index_succeeds(tmp_path):
    """verify with no index yet must not crash: empty store -> pass at wip."""
    root = make_untraced_repo(tmp_path)
    proc = run_trace(root, "verify", "--changed")
    assert proc.returncode == 0
    assert "verify: pass" in proc.stdout
    proc = run_trace(root, "verify", "--all")
    assert proc.returncode == 0
    assert "verify: pass" in proc.stdout


def test_verify_exit_2_on_config_error(tmp_path):
    """A broken .trace/trace.toml is a configuration error (exit 2, TL100)."""
    root = make_untraced_repo(tmp_path)
    (root / ".trace").mkdir(parents=True, exist_ok=True)
    (root / ".trace" / "trace.toml").write_text("schema_version = [broken\n", encoding="utf-8")
    proc = run_trace(root, "verify", "--all")
    assert proc.returncode == 2
    assert "TL100" in proc.stdout
    assert "ERROR" in proc.stdout


def test_verify_exit_3_on_corrupt_index(tmp_path):
    """A corrupt index database is exit 3 (index unavailable)."""
    root = make_untraced_repo(tmp_path)
    cache = root / ".trace" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cache.joinpath("index.sqlite3").write_bytes(b"\x00garbage-not-a-database\xff" * 32)
    proc = run_trace(root, "verify", "--all")
    assert proc.returncode == 3
    assert "index unavailable" in proc.stdout or "index unavailable" in proc.stderr


def test_new_invalid_type_exits_2(tmp_path):
    """`trace new` with an unknown node type is a usage error (exit 2)."""
    root = setup_auth_repo(tmp_path)
    proc = run_trace(root, "new", "bogus", "--name", "x")
    assert proc.returncode == 2


def test_context_unknown_id_exits_1(tmp_path):
    """`trace context` for an unknown id fails cleanly (exit 1)."""
    root = setup_auth_repo(tmp_path)
    proc = run_trace(root, "context", "REQ-DOES-NOT-EXIST")
    assert proc.returncode == 1
    assert "unknown trace id" in proc.stderr or "unknown trace id" in proc.stdout


def make_untraced_repo(tmp_path):
    """A small git repo with a plain source file (no markers)."""
    from tests.conftest import make_git_repo

    return make_git_repo(
        tmp_path,
        {
            "src/a.py": "def foo():\n    return 1\n",
        },
    )
