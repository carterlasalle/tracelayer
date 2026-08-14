"""Spec 58: structured performance diagnostics under --debug (observable contract)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace


# trace:v1 id=test.dogfood.tests.integration.test_debug_flag.py type=test
def test_debug_index_emits_stage_stats(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "app.py": "# \x74race:v1 id=impl.a\n\ndef f():\n    return 1\n",
        },
    )
    r = run_trace(repo, "--debug", "index", "--all")
    assert r.returncode == 0
    # The first-run hint may precede the JSON line on unconfigured repos.
    payload = json.loads(r.stderr.strip().splitlines()[-1])
    assert payload["command"] == "index"
    assert payload["nodes"] == 1
    assert "files_scanned" in payload["per_stage"]
    assert "symbols_attached" in payload["per_stage"]


def test_debug_verify_emits_diagnostics_count(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "app.py": "# \x74race:v1 id=impl.a\n\ndef f():\n    return 1\n",
        },
    )
    assert run_trace(repo, "index", "--all").returncode == 0
    r = run_trace(repo, "--debug", "verify", "--all")
    assert r.returncode == 0
    payload = json.loads(r.stderr.strip().splitlines()[-1])
    assert payload["command"] == "verify"
    assert payload["lifecycle"] == "wip"
    assert "diagnostics" in payload


def test_no_debug_keeps_stderr_clean(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "app.py": "# \x74race:v1 id=impl.a\n\ndef f():\n    return 1\n",
        },
    )
    r = run_trace(repo, "index", "--all")
    assert r.returncode == 0
    # No JSON diagnostics on stderr; at most the first-run hint.
    assert "files_scanned" not in r.stderr
    assert r.stderr.strip() == "" or "trace init" in r.stderr
