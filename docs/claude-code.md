# Claude Code Integration
<!-- trace:v1 id=doc.tracelayer.claude.code -->

This document describes the reference Claude Code adapter. The adapter lives
in `adapters/claude-code/` and owns harness-specific serialization; the core
`trace hook` protocol is stable and harness-independent.

## What you get

- **SessionStart** — announces TraceLayer health at the start of every
  session (broken refs, stale traces, policy state) in under 400 characters.
- **UserPromptSubmit** — deterministic search over each prompt; injects
  likely existing trace nodes before the agent searches the repository.
- **PreToolUse (Write|Edit)** — blocks the first edit of protected traced
  behavior when the agent has not loaded `trace context <id>`.
- **PostToolUse (Write|Edit)** — detects changed trace nodes, marks linked
  verification dirty, and prints the exact next step.
- **PostToolBatch** — one grouped impact summary per edit batch.
- **Stop** — runs the verify gate before the task may complete.

## Install

```bash
# 1. Initialize the project (never overwrites existing config)
trace init --claude

# 2. Confirm the generated hook configuration
cat .claude/settings.json
```

`trace init --claude` copies the hook configuration from
`adapters/claude-code/settings.template.json` into `.claude/settings.json`.
If you prefer to wire it by hand, copy the template and adjust the command
prefix to match your environment (`uv run trace ...` works when the project
uses `uv`).

## Hook configuration

The template wires every event to the same underlying command,
`uv run trace hook <event> --format claude`:

```json
{
  "hooks": {
    "SessionStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "uv run trace hook session-start --format claude"}]}
    ],
    "UserPromptSubmit": [
      {"matcher": "", "hooks": [{"type": "command", "command": "uv run trace hook prompt-context --format claude"}]}
    ],
    "PreToolUse": [
      {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "uv run trace hook pre-mutation --format claude"}]}
    ],
    "PostToolUse": [
      {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "uv run trace hook post-mutation --format claude"}]}
    ],
    "PostToolBatch": [
      {"hooks": [{"type": "command", "command": "uv run trace hook post-batch --format claude"}]}
    ],
    "Stop": [
      {"matcher": "", "hooks": [{"type": "command", "command": "uv run trace hook stop --format claude"}]}
    ]
  }
}
```

The `--format claude` output envelope is `{"decision": ..., "output": ...}`
so Claude Code can render the text and honor the allow/block decision.

## Schema stability note

Exact Claude hook schemas (hook names, matcher syntax, `additionalContext`
mechanics) may evolve. The adapter owns that serialization and MUST be
verified against the current Claude Code contract at adapter release time.
The core protocol never depends on a specific harness schema (spec 23).

## Session state

Context acknowledgements and blocked-edit state are stored per
session/task in `.trace/cache/` (git-ignored). `trace context <id>` records
the load; the block-once guard then allows the retry.

## Verification

```bash
uv run trace hook session-start --format claude   # smoke test any event
```

Run this after wiring to confirm the tool is on `PATH` and the project is
initialized. Hook outputs are bounded and sanitized by the engine — they are
templates plus repository data, never privileged instructions.
