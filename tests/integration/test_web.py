"""trace web: local 3D graph UI integration tests."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tests.conftest import make_git_repo, run_trace

TRACE_BIN = str(Path(sys.executable).parent / "trace")


# trace:v1 id=test.dogfood.tests.integration.test_web.py type=test
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fetch(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        ctype = resp.headers.get("Content-Type", "")
        return json.loads(body) if "json" in ctype else body


def _indexed_repo(tmp_path) -> Path:
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-AUTH-017 - Rotation\n\n<!-- \x74race:v1 id=REQ-AUTH-017 type=requirement work=WORK-AUTH-237 -->\n",
            "src/auth.py": (
                "# \x74race:v1 id=impl.auth.rotate work=WORK-AUTH-237 satisfies=REQ-AUTH-017\n"
                "def rotate(t):\n    return f'rotated-{t}'\n"
            ),
            "test_a.py": (
                "# \x74race:v1 id=test.auth.rotate verifies=REQ-AUTH-017 exercises=impl.auth.rotate\n"
                "def test_r():\n    assert rotate('x') == 'rotated-x'\n"
            ),
        },
    )
    (repo / ".trace").mkdir(parents=True)
    (repo / ".trace" / "work.toml").write_text(
        '[work."WORK-AUTH-237"]\ntitle = "Refresh token rotation"\n',
        encoding="utf-8",
    )
    assert run_trace(repo, "index", "--all").returncode == 0
    return repo


def _spawn_web(root: Path) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    proc = subprocess.Popen(
        [TRACE_BIN, "--root", str(root), "web", "--no-open", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            _fetch(base + "/api/graph")
            return proc, port
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("web server did not come up")


def test_web_graph_markers_only(tmp_path):
    root = _indexed_repo(tmp_path)
    proc, port = _spawn_web(root)
    try:
        data = _fetch(f"http://127.0.0.1:{port}/api/graph")
        ids = {n["id"] for n in data["nodes"]}
        assert {"REQ-AUTH-017", "WORK-AUTH-237", "impl.auth.rotate", "test.auth.rotate"} <= ids
        preds = {(e["source"], e["target"], e["predicate"]) for e in data["edges"]}
        assert ("impl.auth.rotate", "REQ-AUTH-017", "satisfies") in preds
        assert ("impl.auth.rotate", "WORK-AUTH-237", "work") in preds
        assert ("test.auth.rotate", "REQ-AUTH-017", "verifies") in preds
        assert ("test.auth.rotate", "impl.auth.rotate", "exercises") in preds
        # markers only: no structural predicates ever leak into the payload
        assert all(e["predicate"] not in ("calls", "imports") for e in data["edges"])
    finally:
        proc.terminate()


def test_web_serves_html_and_node_detail(tmp_path):
    root = _indexed_repo(tmp_path)
    proc, port = _spawn_web(root)
    try:
        html = _fetch(f"http://127.0.0.1:{port}/")
        assert "3d-force-graph" in html and "TraceLayer" in html
        vendor = _fetch(f"http://127.0.0.1:{port}/vendor/3d-force-graph.min.js")
        assert "three" in vendor or "ForceGraph" in vendor
        detail = _fetch(f"http://127.0.0.1:{port}/api/node/impl.auth.rotate")
        assert detail["id"] == "impl.auth.rotate"
        up = {(u["id"], u["predicate"]) for u in detail["upstream"]}
        assert ("REQ-AUTH-017", "satisfies") in up
        assert ("WORK-AUTH-237", "work") in up
        down = {(d["id"], d["predicate"]) for d in detail["downstream"]}
        assert ("test.auth.rotate", "exercises") in down
    finally:
        proc.terminate()


def test_web_unknown_node_404(tmp_path):
    root = _indexed_repo(tmp_path)
    proc, port = _spawn_web(root)
    try:
        try:
            _fetch(f"http://127.0.0.1:{port}/api/node/nope.nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        proc.terminate()


# trace:v1 id=test.web.work-fixture type=test verifies=REQ-work-view-panel
def _indexed_repo_with_tasks(tmp_path) -> Path:
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-AUTH-017 - Rotation\n\n<!-- \x74race:v1 id=REQ-AUTH-017 type=requirement work=WORK-AUTH-237 -->\n",
            "plan.md": (
                "## TASK-1 - First task\n\n"
                "<!-- \x74race:v1 id=TASK-1 type=task state=TODO work=WORK-AUTH-237 -->\n\n"
                "## TASK-2 - Second task\n\n"
                "<!-- \x74race:v1 id=TASK-2 type=task state=TODO work=WORK-AUTH-237 blocked_by=TASK-1 -->\n\n"
                "## VALUE-1 - Pkg version\n\n"
                "<!-- \x74race:v1 id=VALUE-1 type=value canonical_source=vals.toml::pkg.version value=2.0 work=WORK-AUTH-237 -->\n\n"
                "## ANTI-1 - No direct glob\n\n"
                "<!-- \x74race:v1 id=ANTI-1 type=anti_pattern state=ACTIVE work=WORK-AUTH-237 -->\n"
            ),
            "vals.toml": '[pkg]\nversion = "2.0"\n',
        },
    )
    (repo / ".trace").mkdir(parents=True)
    (repo / ".trace" / "work.toml").write_text(
        '[work."WORK-AUTH-237"]\ntitle = "Refresh token rotation"\n',
        encoding="utf-8",
    )
    assert run_trace(repo, "index", "--all").returncode == 0
    return repo


# trace:v1 id=test.web.work-endpoint type=test verifies=REQ-work-view-panel
def test_web_work_ready_endpoint(tmp_path):
    root = _indexed_repo_with_tasks(tmp_path)
    proc, port = _spawn_web(root)
    try:
        ready = _fetch(f"http://127.0.0.1:{port}/api/work/WORK-AUTH-237/ready")
        assert ready["work"] == "WORK-AUTH-237"
        assert "TASK-1" in ready["ready"]
        assert "TASK-2" in ready["blocked"]
        html = _fetch(f"http://127.0.0.1:{port}/")
        assert 'id="workview"' in html and "showWork" in html
        try:
            _fetch(f"http://127.0.0.1:{port}/api/work/WORK-NOPE/ready")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        proc.terminate()


# trace:v1 id=test.web.node-related type=test verifies=REQ-richer-node-detail
def test_web_node_detail_has_related_and_adjacent(tmp_path):
    root = _indexed_repo_with_tasks(tmp_path)
    proc, port = _spawn_web(root)
    try:
        detail = _fetch(f"http://127.0.0.1:{port}/api/node/TASK-2")
        sections = {(r["section"], r["id"]) for r in detail["related"]}
        assert ("Blocked by", "TASK-1") in sections
        assert "adjacent" in detail
    finally:
        proc.terminate()


# trace:v1 id=test.web.facts-endpoint type=test verifies=REQ-confined-live-fact-verification
def test_web_facts_endpoint(tmp_path):
    root = _indexed_repo_with_tasks(tmp_path)
    proc, port = _spawn_web(root)
    try:
        data = _fetch(f"http://127.0.0.1:{port}/api/facts")
        by_id = {f["id"]: f for f in data["facts"]}
        assert by_id["VALUE-1"]["status"] == "CURRENT"
        html = _fetch(f"http://127.0.0.1:{port}/")
        assert "showFacts" in html and "showKnowledge" in html
    finally:
        proc.terminate()


# trace:v1 id=test.web.knowledge-endpoint type=test verifies=REQ-transitive-knowledge-relevance
def test_web_knowledge_endpoint(tmp_path):
    root = _indexed_repo_with_tasks(tmp_path)
    proc, port = _spawn_web(root)
    try:
        data = _fetch(f"http://127.0.0.1:{port}/api/knowledge")
        by_id = {k["id"]: k for k in data["knowledge"]}
        assert by_id["ANTI-1"]["type"] == "anti_pattern"
        assert by_id["ANTI-1"]["state"] == "ACTIVE"
    finally:
        proc.terminate()
