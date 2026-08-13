# TraceLayer × Pi

[Pi](https://github.com/earendil-works/pi) is a minimal open-source coding agent
harness with an extension/hook system (`@earendil-works/pi-coding-agent`).
This adapter wires TraceLayer's deterministic hook events into Pi using the
Claude-Code-compatible command-hook format supported by community `pi-hooks`
packages (e.g. `@hsingjui/pi-hooks`, `@fyeeme/pi-hooks`).

## Install

```bash
pi install npm:@hsingjui/pi-hooks   # or @fyeeme/pi-hooks
```

Copy the hook configuration into your project:

```bash
cp adapters/pi/hooks.template.json .pi/hooks.json   # project-level
# or merge into ~/.pi/agent/settings.json for user-level hooks
cp adapters/pi/hooks/trace-hook.sh .pi/trace-hook.sh
chmod +x .pi/trace-hook.sh
```

Reload the agent (`/reload`) after changing hook configuration.

## Events wired

| Pi event | TraceLayer hook | Effect |
|---|---|---|
| `SessionStart` | `session-start` | Health summary injected at session start |
| `UserPromptSubmit` | `prompt-context` | Relevant trace context suggested before broad search |
| `PreToolUse` | `pre-mutation` | Blocks the first mutation of protected traced behavior until `trace context` was loaded |
| `PostToolUse` | `post-mutation` | Dirty-verification guidance after edits |
| `Stop` | `stop` | Blocks completion while `trace verify` has blocking failures |

## Hook JSON contract

Pi hook commands receive a JSON payload on stdin:

```json
{
  "session_id": "session-file-path",
  "cwd": "/repo",
  "hook_event_name": "PreToolUse",
  "tool_name": "edit",
  "tool_input": {"file_path": "src/app.py"},
  "tool_use_id": "toolu_123"
}
```

`trace-hook.sh` normalizes this payload (mapping `tool_input.file_path` to
`path`), runs `trace hook <event> --format json`, and emits the Pi contract:

```json
{"permissionDecision": "deny", "permissionDecisionReason": "..."}
```

Blocking decisions use `deny`; informational events print their bounded
output text so the harness can surface it.

## Configuration template

```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": ".pi/trace-hook.sh session-start pi"}
    ],
    "UserPromptSubmit": [
      {"type": "command", "command": ".pi/trace-hook.sh prompt-context pi"}
    ],
    "PreToolUse": [
      {"type": "command", "command": ".pi/trace-hook.sh pre-mutation pi"}
    ],
    "PostToolUse": [
      {"type": "command", "command": ".pi/trace-hook.sh post-mutation pi"}
    ],
    "Stop": [
      {"type": "command", "command": ".pi/trace-hook.sh stop pi"}
    ]
  }
}
```

Notes:

- The `trace` CLI must be on `PATH` (or the script's `uv run trace` fallback
  must find the project). Prefer installing TraceLayer in the project
  (`uv tool install` or a dev dependency) so `uv run trace` resolves.
- The pre-mutation guard is most effective for file-editing tools. Pi hook
  packages differ in which tools they cover; the Stop gate and CI remain the
  authoritative enforcement points regardless of coverage.
