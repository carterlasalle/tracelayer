# TraceLayer × Oh My Pi (omp)
<!-- trace:v1 id=doc.tracelayer.omp-adapter -->
[Oh My Pi](https://github.com/can1357/oh-my-pi) (package
`@oh-my-pi/pi-coding-agent`) provides a native extension API
(`pi.on("tool_call" | "tool_result" | "session_stop", ...)`). This adapter
enforces TraceLayer's authoring gate, post-edit coaching, and the
fail-closed completion gate inside omp:

- **`trace-gate.ts`** — the extension factory: pre-mutation blocking gate
  (`tool_call`), post-edit coaching + obligation resolution
  (`tool_result`), and the stop gate (`session_stop`). Installed as a
  proper extension **package** (`extensions/tracelayer/` with a
  `package.json` manifest) so omp's discovery loads it by manifest and
  plugin tooling sees the version.
- **`hooks.yaml`** — the `pi-yaml-hooks` template: declarative,
  non-blocking events (session lifecycle, prompt intake, file-change
  indexing).

<!-- trace:exempt reason=document-structure -->
## Install (TraceLayer-managed, recommended)

`trace install --agent omp` (project scope) / `trace install --agent omp
--global` writes everything into the runtime's real discovery locations:

| Artifact | Project | Global |
|---|---|---|
| Extension package (factory + manifest) | `.omp/extensions/tracelayer/` | `~/.omp/agent/extensions/tracelayer/` |
| YAML hooks | `.omp/hook/hooks.yaml` | `~/.omp/agent/hook/hooks.yaml` |
| Skill | `.omp/skills/traceability/` | `~/.omp/agent/skills/traceability/` |

The manifest version tracks the tracelayer release, so
`omp list`/`omp plugin list` show the installed version.

<!-- trace:exempt reason=document-structure -->
## Updates

- **`tracelayer update`** (or `trace update`) is the canonical refresh:
  it force-re-copies every omp artifact (extension package, YAML hooks,
  skill) and removes the legacy pre-package layout
  (`extensions/trace-gate.ts` raw file, dead `~/.omp/{extensions,hook,
  skills}` globals) so nothing registers twice. Run it after upgrading the
  tool.
- **Plugin flow** (`omp install`): the package is also a valid omp
  extension package, so `omp install ./adapters/oh-my-pi` (or the bundled
  copy at `<site-packages>/tracelayer/_adapters/oh-my-pi/`) works. Local
  paths are symlinked and watched — content updates flow live; re-run
  `omp install <path> --force` to re-record the version, or use
  `omp update --plugins` for npm/marketplace sources.

<!-- trace:exempt reason=document-structure -->
## Extension package

`trace install` generates `extensions/tracelayer/package.json`:

```json
{
  "name": "tracelayer",
  "version": "0.2.5",
  "pi":   { "extensions": ["./trace-gate.ts"] },
  "omp":  { "extensions": ["./trace-gate.ts"] }
}
```

omp's loader (`discoverExtensionsInDir`) reads `pi.extensions` from a
subdirectory `package.json`; `omp.extensions` is the docs' new key — both
are emitted. The factory is the single source
(`adapters/oh-my-pi/trace-gate.ts`).

<!-- trace:exempt reason=document-structure -->
## Runtime contract (`trace-gate.ts`)

- Uses `node:child_process` `spawnSync` — **not** `Bun.spawnSync`, whose
  `input` option is ignored and would silently drop every hook payload
  (the gate would run with an empty body and never block).
- `tool_call` (write/edit): sends the full proposed mutation
  (`path`, `content` or `old_string`/`new_string`, session id); the engine
  simulates the edit, classifies NEW/MODIFIED boundaries, and either
  returns `allow` + `updatedInput` (single-requirement Write injection) or
  blocks with the authoring plan. Current omp Edit inputs
  (`edits: [{oldText, newText}]`) are mapped onto the engine contract;
  Claude-style `old_string`/`new_string` is still accepted.
- `tool_result`: runs post-mutation (obligation resolution, receipts,
  coaching); Bash/patch results run the working-tree scan. Guidance is
  appended to the tool result the model sees.
- `session_stop`: runs the stop gate (verify + obligations) and blocks
  with the failures (`{ decision: "block", reason }`); the engine's stop
  hook also runs the merge-grade auto-finalizer internally. Diagnostics
  go to stderr (the OMP log).
- Session ids resolve from `ctx.session.id` (legacy) or
  `ctx.sessionManager.getSessionId()` (current `ExtensionContext`).

<!-- trace:exempt reason=document-structure -->
## YAML template (`hooks.yaml`)

```yaml
hooks:
  - event: session.created
    actions:
      - bash: uv run trace hook session-start --format json
  - event: user.prompt.submit
    actions:
      - bash: uv run trace hook prompt-context --format json
  - event: tool.after.edit
    actions:
      - bash: uv run trace hook post-mutation --format json
  - event: tool.after.write
    actions:
      - bash: uv run trace hook post-mutation --format json
  - event: file.changed
    actions:
      - bash: uv run trace index --changed
```

YAML bash hooks are informational (guidance + index freshness); blocking
enforcement lives in the extension factory. Trust project hooks the first
time with `/hooks-trust`, validate with `/hooks-validate`.

<!-- trace:exempt reason=document-structure -->
## Notes

- Hooks assume `trace` is reachable via `uv run` from the repository root
  (or `trace` on PATH for a global tool install).
- Block-once semantics are stored in `.trace/cache/session/`
  (git-ignored); `trace context <id>` records context-load acknowledgement
  per session.
- The stop gate is authoritative: it runs the same `trace verify` policy
  evaluation as CI, so hook coverage gaps cannot silently weaken
  enforcement.
