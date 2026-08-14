"""PostToolUse untraced-behavior nudge integration tests (spec 22.5)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace


# trace:v1 id=test.dogfood.tests.integration.test_hook_nudge.py type=test
def _hook(root, path: str) -> dict:
    r = run_trace(
        root,
        "hook",
        "post-mutation",
        "--format",
        "json",
        input=json.dumps({"path": path}),
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_new_file_with_symbols_teaches_judgment(tmp_path):
    repo = make_git_repo(tmp_path, {"src/old.py": "def old():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "new.py").write_text("def fresh():\n    return 2\n", encoding="utf-8")
    out = _hook(repo, "src/new.py")
    assert out["untraced"] == ["fresh"]
    assert out["new_file"] is True
    assert "NEW ARTIFACT CREATED" in out["output"]
    assert "\x74race:v1" in out["output"]
    assert "Do not trace imports, boilerplate" in out["output"]


def test_requirement_edit_flags_stale_downstream(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-1 - Auth\n\n<!-- \x74race:v1 id=REQ-1 type=requirement -->\n",
            "src/app.py": (
                "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef login():\n    return 1\n"
            ),
            "test_a.py": (
                "# \x74race:v1 id=test.one verifies=REQ-1 exercises=impl.one\n"
                "def test_login():\n    assert login() == 1\n"
            ),
        },
    )
    run_trace(repo, "index", "--all")
    (repo / "req.md").write_text(
        "## REQ-1 - Auth (revised)\n\n<!-- \x74race:v1 id=REQ-1 type=requirement -->\n",
        encoding="utf-8",
    )
    out = _hook(repo, "req.md")
    assert out["stale_downstream"] == {"REQ-1": ["impl.one", "test.one"]}
    assert "Downstream artifacts marked stale" in out["output"]


def test_prompt_records_active_work_and_attaches_it(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-AUTH-017 - Rotation\n\n<!-- \x74race:v1 id=REQ-AUTH-017 type=requirement -->\n",
            "src/old.py": "def old():\n    return 1\n",
        },
    )
    run_trace(repo, "index", "--all")
    env = {"TRACE_SESSION": "sess-attach"}
    r = run_trace(
        repo,
        "hook",
        "prompt-context",
        "--format",
        "json",
        env=env,
        input=json.dumps({"prompt": "Implement WORK-AUTH-237 per REQ-AUTH-017"}),
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["active_work"] == "WORK-AUTH-237"
    assert out["active_requirement"] == "REQ-AUTH-017"
    # New file created after the prompt: guidance carries the work item.
    (repo / "src" / "fresh.py").write_text("def fresh():\n    return 2\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        env=env,
        input=json.dumps({"path": "src/fresh.py"}),
    )
    out = json.loads(r.stdout)
    assert "Active work item: WORK-AUTH-237" in out["output"]
    assert "work=WORK-AUTH-237" in out["output"]
    assert "satisfies=REQ-AUTH-017" in out["output"]


def test_deleted_traced_symbol_with_references_blocks(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-1 - Auth\n\n<!-- \x74race:v1 id=REQ-1 type=requirement -->\n",
            "src/app.py": (
                "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef login():\n    return 1\n"
            ),
            "test_a.py": (
                "# \x74race:v1 id=test.one verifies=REQ-1 exercises=impl.one\n"
                "def test_login():\n    assert login() == 1\n"
            ),
        },
    )
    run_trace(repo, "index", "--all")
    # Marker removed while a test still references it.
    (repo / "src" / "app.py").write_text("def login():\n    return 1\n", encoding="utf-8")
    r = run_trace(
        repo,
        "hook",
        "post-mutation",
        "--format",
        "json",
        input=json.dumps({"path": "src/app.py"}),
    )
    assert r.returncode == 2  # blocked (Claude exit-2 semantics)
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "TRACE DELETION REQUIRES ACTION" in out["output"]
    assert "test.one (exercises)" in out["output"]


def test_deleted_file_without_references_notes_rename(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def only():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "# \x74race:v1 id=impl.only\ndef only():\n    return 1\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").unlink()
    out = _hook(repo, "src/app.py")
    assert out["decision"] == "allow"
    assert out["deleted"] == ["impl.only"]
    assert "preserve the stable trace ID" in out["output"]


def test_added_symbol_to_tracked_file_nudges_only_new(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "def keep():\n    return 1\n\n\ndef added():\n    return 2\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == ["added"]


def test_edit_without_new_symbols_is_silent(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "def keep():\n    # comment-only edit\n    return 1\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == []
    assert out["output"] == ""


def test_traced_symbol_not_nudged(tmp_path):
    repo = make_git_repo(tmp_path, {"src/app.py": "def keep():\n    return 1\n"})
    run_trace(repo, "index", "--all")
    (repo / "src" / "app.py").write_text(
        "# \x74race:v1 id=impl.added satisfies=REQ-1\ndef added():\n    return 2\n",
        encoding="utf-8",
    )
    out = _hook(repo, "src/app.py")
    assert out["untraced"] == []
