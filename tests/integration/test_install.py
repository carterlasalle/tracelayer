"""Global skill/hook installation and first-run guidance (install.py + CLI)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace


def _env(tmp_path, name: str = "home") -> dict[str, str]:
    return {"HOME": str(tmp_path / name), "TRACE_SESSION": "install-test"}


def test_install_list_detects_agents_and_state(tmp_path):
    run_trace(tmp_path, "install", "--list", env=_env(tmp_path))
    r = run_trace(tmp_path, "install", "--list", env=_env(tmp_path))
    assert r.returncode == 0
    assert "claude-code" in r.stdout and "not installed" in r.stdout


def test_install_skill_global_is_idempotent(tmp_path):
    home = _env(tmp_path)
    r = run_trace(tmp_path, "install", "--agent", "claude-code", "--global", "--yes", env=home)
    assert r.returncode == 0, r.stderr
    skill = tmp_path / "home" / ".claude" / "skills" / "traceability" / "SKILL.md"
    assert skill.is_file()
    first = skill.read_text()
    r2 = run_trace(tmp_path, "install", "--agent", "claude-code", "--global", "--yes", env=home)
    assert "already-installed" in r2.stdout
    assert skill.read_text() == first  # untouched on re-run


def test_install_skill_project_scope(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "install", "--agent", "claude-code", "--yes", env=_env(tmp_path))
    assert r.returncode == 0
    assert (repo / ".claude" / "skills" / "traceability" / "SKILL.md").is_file()


def test_install_merges_project_hooks_into_claude_settings(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"apiKeyHelper": "keep-me"}), encoding="utf-8")
    r = run_trace(repo, "install", "--agent", "claude-code", "--yes", env=_env(tmp_path))
    assert r.returncode == 0
    merged = json.loads(settings.read_text())
    assert merged["apiKeyHelper"] == "keep-me"  # unrelated settings preserved
    assert "hooks" in merged and "Stop" in merged["hooks"]
    r2 = run_trace(repo, "install", "--agent", "claude-code", "--yes", env=_env(tmp_path))
    assert "already-installed" in r2.stdout


def test_init_appends_agents_note_without_overwriting(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    (repo / "AGENTS.md").write_text("# My Project\n\nsome docs\n", encoding="utf-8")
    r = run_trace(repo, "init", env=_env(tmp_path))
    assert r.returncode == 0
    content = (repo / "AGENTS.md").read_text()
    assert content.startswith("# My Project\n\nsome docs\n")  # untouched prefix
    assert "trace verify --changed" in content
    r2 = run_trace(repo, "init", env=_env(tmp_path))
    assert "nothing to do" in r2.stdout
    assert (repo / "AGENTS.md").read_text() == content  # no duplicate note


def test_init_skips_agents_note_when_present(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    (repo / "AGENTS.md").write_text(
        "already: trace verify --changed must pass under traceability\n", encoding="utf-8"
    )
    run_trace(repo, "init", env=_env(tmp_path))
    content = (repo / "AGENTS.md").read_text()
    assert content.count("trace verify --changed") == 1


def test_first_run_hint_on_unconfigured_repo(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "status", env=_env(tmp_path))
    assert "trace init" in r.stderr
    assert "trace install" in r.stderr
    r2 = run_trace(repo, "init", env=_env(tmp_path))
    assert "trace init" not in r2.stderr  # no hint for init itself


def test_bundled_skill_dir_found():
    from tracelayer.install import bundled_skill_dir

    skill = bundled_skill_dir()
    assert (skill / "SKILL.md").is_file()
    assert (skill / "README.md").is_file()
    assert (skill / "references" / "marker-protocol.md").is_file()
