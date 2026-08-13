# TraceLayer × Hermes Agent

[Hermes](https://github.com/NousResearch/Hermes-Agent) is the Nous Research
agent. It supports **shell hooks** declared in `~/.hermes/config.yaml` (or a
profile's config): shell commands fired around tool calls, with a consent
model, matchers, and timeouts. This adapter wires TraceLayer's deterministic
hook events into Hermes shell hooks.

## Install

Merge the `hooks:` block from [`config.snippet.yaml`](config.snippet.yaml)
into `~/.hermes/config.yaml` (or your profile config), and install the
wrapper:

```bash
mkdir -p ~/.hermes/hooks
cp adapters/hermes/hooks/trace-hook.sh ~/.hermes/hooks/trace-hook.sh
chmod +x ~/.hermes/hooks/trace-hook.sh
hermes hooks list       # confirm the hooks are registered
hermes hooks doctor     # validate exec bits, allowlist, payload shape
```

Approve the hooks on first use when Hermes prompts (consent is stored in
`~/.hermes/shell-hooks-allowlist.json`).

## Events wired

| Hermes event | Matcher | TraceLayer hook | Effect |
|---|---|---|---|
| `pre_tool_call` | `Bash` | `pre-mutation` | Blocks (exit 2) when trace policy blocks |
| `pre_tool_call` | `Write(*.*)` / `Edit(*.*)` | `pre-mutation` | Blocks the first edit of protected traced behavior until `trace context` was loaded |
| `post_tool_call` | `Write(*.*)` / `Edit(*.*)` | `post-mutation` | Dirty-verification guidance after edits |
| `post_tool_call` | `Bash` | `post-mutation` | Guidance after shell mutations |

## Hermes hook contract

Hermes passes the tool payload via the `CLAUDE_TOOL_INPUT` environment
variable (JSON) and file paths via `CLAUDE_FILE_PATHS`. The wrapper:

1. reads `CLAUDE_TOOL_INPUT` (or stdin),
2. derives `path` from the payload or `CLAUDE_FILE_PATHS`,
3. runs `uv run trace hook <event> --format json`,
4. on a blocking decision prints the reason to stderr and **exits 2**
   (Hermes treats exit 2 as block);
5. otherwise prints the bounded hook output.

## Configuration snippet

```yaml
hooks:
  pre_tool_call:
    - matcher: "Bash"
      command: "~/.hermes/hooks/trace-hook.sh pre-mutation hermes"
      timeout: 30
      hooks_auto_accept: false
    - matcher: "Write(*.*)|Edit(*.*)"
      command: "~/.hermes/hooks/trace-hook.sh pre-mutation hermes"
      timeout: 30
      hooks_auto_accept: false
  post_tool_call:
    - matcher: "Write(*.*)|Edit(*.*)"
      command: "~/.hermes/hooks/trace-hook.sh post-mutation hermes"
      timeout: 30
    - matcher: "Bash"
      command: "~/.hermes/hooks/trace-hook.sh post-mutation hermes"
      timeout: 30
```

## Notes

- The `trace` CLI must be reachable via `uv run` from the repository root;
  for user-level setup, install TraceLayer with `uv tool install tracelayer`
  and call `trace` directly in the command.
- Block-once pre-mutation state lives in `.trace/cache/session/`
  (git-ignored); `trace context <id>` records the acknowledgement.
- The `Stop` gate from the CLI/CI remains authoritative — Hermes shell hooks
  cannot intercept session shutdown, so run `trace verify --changed` as part
  of your completion ritual or CI.
