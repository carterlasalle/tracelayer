"""Global skill/hook installation and first-run guidance (install.py + CLI)."""

from __future__ import annotations

import json

from tests.conftest import make_git_repo, run_trace


# trace:v1 id=test.dogfood.tests.integration.test_install.py type=test
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


# trace:v1 id=test.dogfood.tests.integration.test_install.note-skip type=test
def test_init_skips_agents_note_when_present(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    (repo / "AGENTS.md").write_text(
        "<!-- tracelayer-agent-invariant:v2 -->\n"
        "already: trace verify --changed must pass under traceability\n",
        encoding="utf-8",
    )
    run_trace(repo, "init", env=_env(tmp_path))
    content = (repo / "AGENTS.md").read_text()
    assert content.count("tracelayer-agent-invariant:v2") == 1
    # a v1-era note is upgraded (version-tagged, never silently stale)
    (repo / "AGENTS.md").write_text(
        "trace verify --changed must pass under the active policy\n", encoding="utf-8"
    )
    run_trace(repo, "init", env=_env(tmp_path))
    assert "tracelayer-agent-invariant:v2" in (repo / "AGENTS.md").read_text()


def test_first_run_hint_on_unconfigured_repo(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "status", env=_env(tmp_path))
    assert "trace init" in r.stderr
    assert "trace install" in r.stderr
    r2 = run_trace(repo, "init", env=_env(tmp_path))
    assert "trace init" not in r2.stderr  # no hint for init itself


# trace:v1 id=test.dogfood.tests.integration.test_install.test_install_pi_omp_opencode_hook_assets type=test
def test_install_pi_omp_opencode_hook_assets(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(
        repo,
        "install",
        "--agent",
        "pi",
        "--agent",
        "omp",
        "--agent",
        "opencode",
        "--yes",
        env=_env(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert (repo / ".pi" / "hooks.json").is_file()
    assert (repo / ".pi" / "trace-hook.sh").is_file()
    assert (repo / ".omp" / "hook" / "hooks.yaml").is_file()
    assert (repo / ".omp" / "extensions" / "tracelayer" / "trace-gate.ts").is_file()
    assert (repo / "opencode.json").is_file()
    assert "activate" in r.stdout


def test_install_global_hooks_for_pi(tmp_path):
    home = _env(tmp_path)
    r = run_trace(tmp_path, "install", "--agent", "pi", "--global", "--yes", env=home)
    assert r.returncode == 0
    assert (tmp_path / "home" / ".pi" / "hooks.json").is_file()
    assert (tmp_path / "home" / ".pi" / "agent" / "skills" / "traceability" / "SKILL.md").is_file()


def test_install_update_refreshes(tmp_path):
    home = _env(tmp_path)
    run_trace(tmp_path, "install", "--agent", "claude-code", "--global", "--yes", env=home)
    skill = tmp_path / "home" / ".claude" / "skills" / "traceability" / "SKILL.md"
    first = skill.read_text()
    r = run_trace(
        tmp_path, "install", "--agent", "claude-code", "--global", "--update", "--yes", env=home
    )
    assert r.returncode == 0
    assert skill.read_text() == first  # refresh is content-stable


def test_init_writes_mcp_json_by_default(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "init", env=_env(tmp_path))
    assert r.returncode == 0
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert mcp["mcpServers"]["tracelayer"]["command"] == "tracelayer"
    # re-init is idempotent and preserves other servers
    mcp["mcpServers"]["other"] = {"command": "other"}
    (repo / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    run_trace(repo, "init", env=_env(tmp_path))
    merged = json.loads((repo / ".mcp.json").read_text())
    assert "other" in merged["mcpServers"] and "tracelayer" in merged["mcpServers"]


def test_init_all_skips_nothing(tmp_path):
    (tmp_path / "home" / ".claude" / "skills").mkdir(parents=True)
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    run_trace(repo, "init", "--all", env=_env(tmp_path))
    assert (repo / ".mcp.json").exists()
    assert (repo / ".agents" / "skills" / "traceability" / "SKILL.md").exists()
    assert (repo / ".claude" / "settings.json").exists()


def test_init_no_mcp_skips(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    run_trace(repo, "init", "--no-mcp", env=_env(tmp_path))
    assert not (repo / ".mcp.json").exists()


def test_install_project_writes_mcp_json(tmp_path):
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    run_trace(repo, "install", "--agent", "claude-code", "--yes", env=_env(tmp_path))
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert "tracelayer" in mcp["mcpServers"]


def test_install_agent_without_hook_assets(tmp_path):
    """Agents with no hook assets (cursor) must not crash install."""
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "install", "--agent", "cursor", "--yes", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "cursor: installed" in r.stdout or "cursor: already-installed" in r.stdout


def test_init_installs_skill_and_hooks_for_detected_agents(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    r = run_trace(repo, "init", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "claude-code:" in r.stdout
    assert (repo / ".claude" / "settings.json").exists()
    assert (repo / ".claude" / "skills" / "traceability" / "SKILL.md").exists()
    assert (repo / ".agents" / "skills" / "traceability" / "SKILL.md").exists()
    assert (repo / ".mcp.json").exists()


def test_update_refreshes_installed_copies(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    repo = make_git_repo(tmp_path, {"a.py": "x = 1\n"})
    run_trace(repo, "init", env=_env(tmp_path))
    skill = repo / ".claude" / "skills" / "traceability" / "SKILL.md"
    skill.write_text("stale skill content\n", encoding="utf-8")
    r = run_trace(repo, "update", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "claude-code:" in r.stdout
    assert "stale skill content" not in skill.read_text()
    assert "How enforcement works" in skill.read_text()


# trace:v1 id=test.dogfood.tests.integration.test_install.template-drift type=test
def test_claude_template_matches_canonical_generator(tmp_path):
    """The checked-in Claude adapter template IS the canonical generator's
    output — installed copies and the repo adapter cannot drift."""
    import json
    from pathlib import Path as _Path

    from tracelayer.install import claude_settings_template

    template_path = (
        _Path(__file__).resolve().parents[2] / "adapters" / "claude-code" / "settings.template.json"
    )
    assert json.loads(template_path.read_text(encoding="utf-8")) == json.loads(
        claude_settings_template()
    )
    hooks = json.loads(claude_settings_template())["hooks"]
    bash_matchers = [g["matcher"] for g in hooks["PostToolUse"] if g.get("matcher") == "Bash"]
    assert bash_matchers, "Bash PostToolUse hook must be wired in the canonical config"


def test_bundled_skill_dir_found():
    from tracelayer.install import bundled_skill_dir

    skill = bundled_skill_dir()
    assert (skill / "SKILL.md").is_file()
    assert (skill / "README.md").is_file()
    assert (skill / "references" / "marker-protocol.md").is_file()


# trace:v1 id=test.dogfood.tests.integration.test_install.test_omp_adapter_contract type=test
def test_omp_adapter_contract(tmp_path):
    """The shipped OMP extension must not regress on two runtime failures:

    1. pi.log does not exist in the OMP extension API (crash at runtime);
    2. Bun.spawnSync ignores the `input` option, silently dropping every
       hook payload — the gate must use node:child_process spawnSync so
       paths/content/sessions actually reach the engine.
    It must also map the CURRENT OMP Edit input shape (edits[{oldText,
    newText}]) onto the engine's old_string/new_string contract.
    """
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "install", "--agent", "omp", "--yes", env=_env(tmp_path))
    adapter = (repo / ".omp" / "extensions" / "tracelayer" / "trace-gate.ts").read_text(
        encoding="utf-8"
    )
    assert "pi.log" not in adapter  # the crash: pi.log is not a function
    assert "pi." in adapter  # the extension API surface is still used
    assert 'from "node:child_process"' in adapter  # payload delivery, not Bun.spawnSync
    assert "Bun.spawnSync([" not in adapter  # the broken input-dropping variant
    assert "oldText" in adapter and "newText" in adapter  # current OMP Edit shape
    assert "session_stop" in adapter  # stop-gate event wiring preserved


# trace:v1 id=test.dogfood.tests.integration.test_install.test_omp_extension_package_layout type=test
def test_omp_extension_package_layout(tmp_path):
    """omp installs TraceLayer as an extension PACKAGE (manifest + factory)
    in the runtime's discovery locations, and removes the legacy raw file
    so the factory cannot register twice."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    env = _env(tmp_path)
    run_trace(repo, "install", "--agent", "omp", "--yes", env=env)
    pkg = repo / ".omp" / "extensions" / "tracelayer"
    assert (pkg / "package.json").is_file()
    manifest = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "tracelayer"
    assert manifest["pi"]["extensions"] == ["./trace-gate.ts"]
    assert manifest["omp"]["extensions"] == ["./trace-gate.ts"]
    assert (pkg / "trace-gate.ts").is_file()
    assert not (repo / ".omp" / "extensions" / "trace-gate.ts").exists()  # legacy removed
    assert (repo / ".omp" / "hook" / "hooks.yaml").is_file()
    assert (repo / ".omp" / "skills" / "traceability" / "SKILL.md").is_file()


# trace:v1 id=test.dogfood.tests.integration.test_install.test_omp_global_installs_into_agent_dir type=test
def test_omp_global_installs_into_agent_dir(tmp_path):
    """Global omp installs land in ~/.omp/agent/... — the runtime's global
    dirs — never the dead ~/.omp/{extensions,hook,skills} paths."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    home = tmp_path / "home"
    r = run_trace(
        repo, "install", "--agent", "omp", "--global", "--yes", env=_env(tmp_path, "home")
    )
    assert r.returncode == 0, r.stderr
    agent = home / ".omp" / "agent"
    assert (agent / "extensions" / "tracelayer" / "package.json").is_file()
    assert (agent / "hook" / "hooks.yaml").is_file()
    assert (agent / "skills" / "traceability" / "SKILL.md").is_file()
    # the old dead global locations are gone
    assert not (home / ".omp" / "extensions").exists()
    assert not (home / ".omp" / "hook").exists()
    assert not (home / ".omp" / "skills").exists()


# trace:v1 id=test.dogfood.tests.integration.test_install.test_tracelayer_update_refreshes_omp_package type=test
def test_tracelayer_update_refreshes_omp_package(tmp_path):
    """`trace update` must ACTUALLY update the omp extension package:
    modified content is restored and the legacy file stays removed."""
    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    env = _env(tmp_path)
    run_trace(repo, "install", "--agent", "omp", "--yes", env=env)
    factory = repo / ".omp" / "extensions" / "tracelayer" / "trace-gate.ts"
    orig = factory.read_text(encoding="utf-8")
    factory.write_text(orig.replace("trace-gate", "STALE-trace-gate"), encoding="utf-8")
    r = run_trace(repo, "update", env=env)
    assert r.returncode == 0, r.stderr
    refreshed = factory.read_text(encoding="utf-8")
    assert "STALE" not in refreshed
    assert refreshed == orig
    assert not (repo / ".omp" / "extensions" / "trace-gate.ts").exists()


# trace:v1 id=test.dogfood.tests.integration.test_install.test_omp_package_manifest_version_tracks_release type=test
def test_omp_package_manifest_version_tracks_release(tmp_path):
    """The package manifest version tracks the tracelayer release so plugin
    listings/updates observe new versions."""
    import tracelayer

    repo = make_git_repo(tmp_path, {"README.md": "# home\n"})
    run_trace(repo, "install", "--agent", "omp", "--yes", env=_env(tmp_path))
    manifest = json.loads(
        (repo / ".omp" / "extensions" / "tracelayer" / "package.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == tracelayer.__version__
