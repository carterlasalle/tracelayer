# Claude Code Adapter

Reference integration for Claude Code. Wire every supported hook event to
`uv run trace hook <event> --format claude` so the trace engine runs inside
the agent's own event loop.

## Files

- `settings.template.json` — hook configuration covering SessionStart,
  UserPromptSubmit, PreToolUse (Write|Edit), PostToolUse (Write|Edit),
  PostToolBatch, and Stop, per spec Section 23.
- `../generic-json-hooks/protocol.md` — the underlying generic contract the
  adapter satisfies.

## Install

```bash
trace init --claude
```

`trace init --claude` copies this template to `.claude/settings.json`
(never overwriting an existing file without opt-in). Manual install:

```bash
mkdir -p .claude
cp adapters/claude-code/settings.template.json .claude/settings.json
```

Then adjust the `command` prefix if your project does not use `uv` (the
template assumes `uv run trace ...` on `PATH` in the project environment).

## Behavior per event

| Hook | Effect |
|---|---|
| SessionStart | Health summary (<400 chars): broken refs, stale traces, policy |
| UserPromptSubmit | FTS search over the prompt; injects likely trace nodes or nothing |
| PreToolUse Write\|Edit | Blocks the first edit of protected traced behavior without loaded context; returns `trace context <id>` instruction |
| PostToolUse Write\|Edit | Re-indexes, detects changed trace nodes, marks verification dirty |
| PostToolBatch | One grouped impact summary per batch |
| Stop | Runs `trace verify --changed`; blocks completion on blocking failures |

The engine emits `{"decision": ..., "output": ...}` JSON for `--format
claude`, which Claude Code renders as additional context and honors as a
block decision.

## Schema stability

Exact Claude hook schemas (hook names, matcher syntax, output mechanics) may
evolve. This adapter owns that serialization and MUST be re-verified against
the current Claude Code contract at adapter release time; it is deliberately
kept out of the core protocol (spec 23).

## Smoke test

```bash
echo '{"session_id":"smoke"}' | uv run trace hook session-start --format claude
```

Expect a `{"decision": "allow", "output": "TraceLayer active. ..."}` envelope
with bounded, sanitized text.
