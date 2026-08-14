"""MCP stdio server integration tests (optional adapter)."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from tests.conftest import make_git_repo, run_trace

TRACE_BIN = str(pathlib.Path(sys.executable).parent / "trace")


# trace:v1 id=test.dogfood.tests.integration.test_mcp.py type=test
def _spawn(repo):
    proc = subprocess.Popen(
        [TRACE_BIN, "--root", str(repo), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    return proc


def _exchange(proc, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "server closed without a response"
    return json.loads(line)


def _indexed_repo(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": (
                "## REQ-AUTH-017 - Refresh token rotation\n\n"
                "<!-- \x74race:v1 id=REQ-AUTH-017 type=requirement -->\n\nTokens rotate.\n"
            ),
            "app.py": (
                "# \x74race:v1 id=impl.auth.refresh work=WORK-AUTH-237 satisfies=REQ-AUTH-017\n"
                "def rotate():\n    return 1\n"
            ),
            "test_a.py": (
                "# \x74race:v1 id=test.auth.refresh verifies=REQ-AUTH-017 exercises=impl.auth.refresh\n"
                "def test_r():\n    assert rotate() == 1\n"
            ),
        },
    )
    (repo / ".trace").mkdir(parents=True)
    (repo / ".trace" / "work.toml").write_text(
        '[work."WORK-AUTH-237"]\ntitle = "Implement refresh token rotation"\n',
        encoding="utf-8",
    )
    assert run_trace(repo, "index", "--all").returncode == 0
    return repo


def test_initialize_handshake(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        resp = _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["serverInfo"]["name"] == "tracelayer"
        assert "tools" in resp["result"]["capabilities"]
    finally:
        proc.terminate()


def test_tools_list(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        resp = _exchange(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        assert {"context", "why", "impact", "search", "verify", "status", "index"} <= names
    finally:
        proc.terminate()


def test_context_tool(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        resp = _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "context", "arguments": {"trace_id": "impl.auth.refresh"}},
            },
        )
        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert "REQ-AUTH-017" in text and "WORK-AUTH-237" in text
        payload = json.loads(text)
        assert payload["node_type"] == "implementation"
    finally:
        proc.terminate()


def test_verify_tool(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        resp = _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "verify", "arguments": {"scope": "all"}},
            },
        )
        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert "status" in payload and "diagnostics" in payload
    finally:
        proc.terminate()


def test_unknown_tool_and_method_error(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        resp = _exchange(
            proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}}
        )
        assert resp["error"]["code"] == -32601
        resp = _exchange(proc, {"jsonrpc": "2.0", "id": 6, "method": "bogus"})
        assert resp["error"]["code"] == -32601
    finally:
        proc.terminate()


def test_invalid_json_line_is_ignored(tmp_path):
    repo = _indexed_repo(tmp_path)
    proc = _spawn(repo)
    try:
        _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        proc.stdin.write("this is not json\n")
        proc.stdin.flush()
        resp = _exchange(proc, {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        assert resp["id"] == 7 and resp["result"] == {}
    finally:
        proc.terminate()
