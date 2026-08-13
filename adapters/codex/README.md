# TraceLayer × OpenAI Codex CLI

[Codex CLI](https://github.com/openai/codex) supports lifecycle hooks via a
sidecar `hooks.json` (user-level `~/.codex/hooks.json` or project-level
`.codex/hooks.json`). This adapter wires TraceLayer's deterministic hook
events into Codex.

## Enable hooks

Hooks are **off by default**. Add to `~/.codex/config.toml` (or
`.codex/config.toml`):

```toml
[features]
codex_hooks = true
```

## Install

```bash
mkdir -p .codex/hooks
cp adapters/codex/hooks.json .codex/hooks.json
cp adapters/codex/hooks/trace-hook.sh .codex/hooks/trace-hook.sh
chmod +x .codex/hooks/trace-hook.sh
```

## Events wired

| Codex event | TraceLayer hook | Notes |
|---|---|---|
| `SessionStart` | `session-start` | Health summary injected at session start |
| `UserPromptSubmit` | `prompt-context` | Relevant trace context suggested before broad search |
| `PreToolUse` (matcher `^Bash$`) | `pre-mutation` | **Deny-only** — blocks when trace policy blocks; Codex rejects `allow`/`ask` responses |
| `PostToolUse` | `post-mutation` | Observe-only: prints dirty-verification guidance |
| `Stop` | `stop` | Blocks completion while `trace verify` has blocking failures |

## Codex hook contract

Codex sends a JSON payload on stdin:

```json
{
  "session_id": "session-file-path",
  "transcript_path": "...",
  "cwd": "/repo",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "..."},
  "tool_use_id": "toolu_123"
}
```

`trace-hook.sh` normalizes the payload, runs `trace hook <event> --format
json`, and emits the Codex contract:

```json
{"decision": "deny", "reason": "..."}
```

Informational events print their bounded output text as additional context
(`additionalContextLimit` in the template bounds it).

## Known Codex limitations (documented upstream)

- `PreToolUse` fires **only for `Bash`** — not for file edits, reads, or MCP
  tools. The pre-mutation guard therefore cannot gate file edits in Codex;
  the `Stop` gate and CI remain the authoritative enforcement points.
- `PreToolUse` can only **deny**; `allow`/`ask`/`updatedInput` responses are
  rejected by the Codex parser.
- `PostToolUse` is observe-only; it cannot rewrite tool output.

## Verification

```bash
codex login status
# Run a session; watch for the session-start health line and Stop-gate behavior.
trace status   # confirm the graph is indexed before relying on hook output
```
