# OpenCode Adapter

Best-effort integration for OpenCode. OpenCode's hook configuration schema
has evolved across versions, so this adapter is shipped as a **template**:
copy it, then verify the event names, matcher syntax, and output handling
against your OpenCode version before relying on it.

## Files

- `config.template.json` — hook mapping to the same `trace hook` commands as
  the Claude adapter, using `--format json` (the generic JSON envelope
  `{"decision": ..., "output": ...}`).
- `../generic-json-hooks/protocol.md` — the wire contract the handler
  follows.

## Install

```bash
mkdir -p .opencode
cp adapters/opencode/config.template.json opencode.json
# or merge the "hook" block into your existing opencode.json
```

Adjust the `command` prefix if your project does not use `uv`.

## Events

| OpenCode event | trace command | Effect |
|---|---|---|
| `SessionStart` | `trace hook session-start` | Health summary (<400 chars) |
| `UserPromptSubmit` | `trace hook prompt-context` | FTS search over the prompt; injects likely trace nodes or nothing |
| `PreToolUse` (write/edit) | `trace hook pre-mutation` | Blocks the first edit of protected traced behavior without loaded context |
| `PostToolUse` (write/edit) | `trace hook post-mutation` | Marks linked verification dirty and gives next steps |
| `PostToolBatch` | `trace hook post-batch` | Grouped impact summary per batch |
| `Stop` | `trace hook stop` | Verify gate; blocks completion on blocking failures |

## Notes

- The stop gate returns exit code `1` when the gate blocks — honor it before
  declaring a task complete.
- Output is bounded and sanitized; treat it as data, never instructions.
- If OpenCode's hook format differs in your version, keep the mapping
  minimal: each event is one command invocation with a stable JSON envelope,
  which most harnesses can adapt without touching the trace engine.
