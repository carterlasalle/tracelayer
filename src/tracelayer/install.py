"""Global skill and hook installation (skills.sh-compatible).

`trace install` copies the canonical traceability skill into agent skill
directories (global or project scope) and, where the harness supports it,
merges hook configuration into the harness settings file. `trace init` uses
the same helpers to append the repository invariant to AGENTS.md/CLAUDE.md.

The skill and hook templates are bundled with the installed package so the
tool works standalone; in a source checkout the repository copies are used.
"""

from __future__ import annotations

import json
import os
import shutil
from importlib import resources
from pathlib import Path

# One-line invariant appended to AGENTS.md / CLAUDE.md by `trace init`
# (spec 20.1). Detection is by the `trace verify --changed` token.
AGENTS_MD_NOTE = (
    "This repository uses mandatory semantic traceability. Trace integrity is "
    "part of the Definition of Done. Follow the repository traceability skill "
    "and any trace instructions injected by hooks. Do not invent trace fields, "
    "replace stable IDs during refactors, or remove markers to silence "
    "validation. Before completing implementation work, `trace verify --changed` "
    "must pass under the active policy."
)

# Agent registry: ids, skill dirs (global/project), and hook config kind.
# Global paths follow the skills.sh agent directory table; project scope
# defaults to .agents/skills for agents that use it.
AGENTS: dict[str, dict[str, str]] = {
    "claude-code": {
        "global": "~/.claude/skills",
        "project": ".claude/skills",
        "hooks": "claude",
    },
    "codex": {
        "global": "~/.codex/skills",
        "project": ".agents/skills",
        "hooks": "codex",
    },
    "pi": {"global": "~/.pi/agent/skills", "project": ".pi/skills"},
    "omp": {"global": "~/.omp/skills", "project": ".omp/skills"},
    "hermes-agent": {"global": "~/.hermes/skills", "project": ".hermes/skills"},
    "opencode": {"global": "~/.config/opencode/skills", "project": ".agents/skills"},
    "cursor": {"global": "~/.cursor/skills", "project": ".agents/skills"},
    "generic": {"global": "~/.agents/skills", "project": ".agents/skills"},
}

# Executables used to auto-detect installed agents.
_AGENT_BINARIES: dict[str, str] = {
    "claude-code": "claude",
    "codex": "codex",
    "pi": "pi",
    "omp": "omp",
    "hermes-agent": "hermes",
    "opencode": "opencode",
    "cursor": "cursor",
}


def expand(path: str) -> Path:
    """Expand ~ and resolve an agent directory path."""
    return Path(os.path.expanduser(path))


def bundled_skill_dir() -> Path:
    """The canonical skill source: bundled with the package, else repo checkout."""
    try:
        ref = resources.files("tracelayer") / "_skills" / "traceability"
        # Filesystem packages return a real pathlib.Path; wheels are always
        # unpacked by uv/pip, so str(ref) is the on-disk location.
        p = Path(str(ref))
        if p.is_dir():
            return p
    except (ModuleNotFoundError, TypeError, OSError):
        pass
    repo = Path.cwd() / "skills" / "traceability"
    if repo.is_dir():
        return repo
    raise FileNotFoundError("traceability skill not found (broken installation)")


def detect_agents() -> list[str]:
    """Agents that appear installed: skill dir exists or binary on PATH."""
    found: list[str] = []
    for agent, binary in _AGENT_BINARIES.items():
        home_dir = expand(AGENTS[agent]["global"])
        if home_dir.exists() or _on_path(binary):
            found.append(agent)
    return sorted(found)


def _on_path(binary: str) -> bool:
    return any((Path(p) / binary).is_file() for p in os.environ.get("PATH", "").split(":") if p)


def skill_installed(agent: str, root: Path | None = None) -> Path | None:
    """Path of an installed skill for the agent, or None."""
    spec = AGENTS[agent]
    base = expand(spec["global"]) if root is None else (root / spec["project"])
    candidate = base / "traceability"
    return candidate if (candidate / "SKILL.md").is_file() else None


def install_skill(
    agent: str,
    root: Path | None = None,
    *,
    link: bool = False,
    force: bool = False,
) -> tuple[str, Path]:
    """Install the skill for one agent. Returns (status, target path).

    status: "installed" | "already-installed" | "replaced".
    """
    spec = AGENTS[agent]
    base = expand(spec["global"]) if root is None else (root / spec["project"])
    dst = base / "traceability"
    src = bundled_skill_dir()
    if dst.exists() and not force:
        return "already-installed", dst
    shutil.rmtree(dst, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    if link:
        os.symlink(src, dst)
        status = "replaced" if force else "installed"
    else:
        shutil.copytree(src, dst)
        status = "replaced" if force else "installed"
    return status, dst


# --------------------------------------------------------------------------
# Hook config (JSON-merge based): claude-code settings.json, codex hooks.json
# --------------------------------------------------------------------------


def claude_hook_settings() -> dict:
    """Claude Code settings hooks block (mirrors adapters/claude-code)."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run trace hook session-start --format claude",
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run trace hook prompt-context --format claude",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run trace hook pre-mutation --format claude",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run trace hook post-mutation --format claude",
                        }
                    ],
                }
            ],
            "PostToolBatch": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run trace hook post-batch --format claude",
                        }
                    ]
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": "uv run trace hook stop --format claude"}
                    ],
                }
            ],
        },
    }


def codex_hooks_config() -> dict:
    """Codex hooks.json (requires `[features] codex_hooks = true` in config.toml)."""
    return {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "uv run trace hook session-start --format codex",
                        "statusMessage": "TraceLayer: session health",
                        "additionalContextLimit": 1000,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "uv run trace hook prompt-context --format codex",
                        "statusMessage": "TraceLayer: prompt context",
                        "additionalContextLimit": 1500,
                    }
                ],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "^Bash$",
                "hooks": [
                    {
                        "type": "command",
                        "command": "uv run trace hook pre-mutation --format codex",
                        "statusMessage": "TraceLayer: pre-mutation guard",
                        "timeout": 30,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "^Bash$",
                "hooks": [
                    {
                        "type": "command",
                        "command": "uv run trace hook post-mutation --format codex",
                        "statusMessage": "TraceLayer: post-mutation guidance",
                        "additionalContextLimit": 1500,
                        "timeout": 30,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "uv run trace hook stop --format codex",
                        "statusMessage": "TraceLayer: stop gate",
                        "timeout": 60,
                    }
                ],
            }
        ],
    }


def hook_config_for(agent: str, root: Path | None = None) -> tuple[Path, dict] | None:
    """(settings file, config) for agents with JSON hooks, else None.

    Project scope (root given) writes into the repository (e.g.
    <repo>/.claude/settings.json); global scope writes into the user dir.
    """
    spec = AGENTS[agent]
    kind = spec.get("hooks")
    if kind == "claude":
        base = root / ".claude" if root is not None else expand("~/.claude")
        return base / "settings.json", claude_hook_settings()
    if kind == "codex":
        base = root / ".codex" if root is not None else expand("~/.codex")
        return base / "hooks.json", codex_hooks_config()
    return None


def merge_json_file(path: Path, config: dict) -> tuple[str, Path]:
    """Merge `config` into the JSON file at `path` (create if missing).

    Merges top-level keys; `hooks` is replaced wholesale (deterministic and
    idempotent). Never drops unrelated settings.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
    else:
        existing = {}
    if existing.get("hooks") == config.get("hooks"):
        return "already-installed", path
    existing.update(config)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return "installed", path


# --------------------------------------------------------------------------
# Repository invariant (AGENTS.md / CLAUDE.md) — append-only
# --------------------------------------------------------------------------


def note_present(text: str) -> bool:
    """True when the repository invariant already appears in the file."""
    return "trace verify --changed" in text and "traceability" in text.lower()


def append_agents_note(root: Path) -> tuple[str, Path | None]:
    """Append the invariant to AGENTS.md or CLAUDE.md (append-only).

    Returns ("appended"|"already-present"|"no-agent-file", path).
    """
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if note_present(text):
            return "already-present", path
        block = "\n\n" + AGENTS_MD_NOTE + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
        return "appended", path
    return "no-agent-file", None
