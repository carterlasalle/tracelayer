# TraceLayer × Oh My Pi (omp)

[Oh My Pi](https://github.com/can1357/oh-my-pi) is a batteries-included fork of
Pi with an in-process LSP, DAP debugging, role-based models, and a rich hook
subsystem. This adapter provides:

- a **YAML hooks** template (`hooks.yaml`) for the `pi-yaml-hooks` plugin —
  declarative, non-blocking events (session lifecycle, prompt intake, file
  changes);
- a **native TypeScript hook** (`trace-gate.ts`) that implements the blocking
  pre-mutation guard and the fail-closed Stop gate via the `HookAPI`
  (`pi.on("tool_call", ...)`).

## Install

```bash
omp plugin install pi-yaml-hooks        # for the YAML template (optional)
mkdir -p .omp/hook .omp/extensions
cp adapters/oh-my-pi/hooks.yaml .omp/hook/hooks.yaml          # project-level YAML
cp adapters/oh-my-pi/trace-gate.ts .omp/extensions/trace-gate.ts   # native gate
```

Trust project hooks the first time with `/hooks-trust` (project hooks can run
arbitrary bash). Validate with `/hooks-validate` and inspect with
`/hooks-status`.

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

YAML bash hooks are informational: they surface TraceLayer guidance and keep
the index fresh, but they do not block tool calls. Blocking enforcement lives
in the TypeScript hook.

## Native TypeScript gate (`trace-gate.ts`)

The native hook intercepts `tool_call` before every tool, resolves the target
file from the tool input, and consults the TraceLayer pre-mutation hook:

- first edit of protected traced behavior without loaded context → `block`
  with the exact `trace context` command;
- after `trace context` was loaded (recorded in session state), edits proceed;
- `stop`-equivalent enforcement is provided by the `Stop` hook event wiring
  (`pi.on("session_shutdown", ...)` runs `trace verify --changed` and blocks
  the shutdown on blocking failures — configure your harness to treat it as
  the completion gate).

```typescript
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

export default function hook(pi: HookAPI): void {
  const run = (args: string[], input: string): { code: number; out: string } => {
    // Bun ships with the omp runtime; fall back to child_process for node.
    const res = Bun.spawnSync(["uv", "run", "trace", ...args], {
      stdin: "pipe",
      input,
    });
    return { code: res.exitCode ?? -1, out: res.stdout.toString() };
  };

  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "edit" && event.toolName !== "write") return;
    const filePath = String(event.input?.file_path ?? event.input?.path ?? "");
    if (!filePath) return;
    const payload = JSON.stringify({
      path: filePath,
      line: event.input?.line ?? null,
      session_id: ctx.session?.id ?? "default",
    });
    const res = run(["hook", "pre-mutation", "--format", "json"], payload);
    if (res.code !== 0) {
      try {
        const d = JSON.parse(res.out);
        return { block: true, reason: d.output ?? "trace policy blocks this edit" };
      } catch {
        return { block: true, reason: "trace policy blocks this edit" };
      }
    }
  });

  pi.on("session_shutdown", async (event, ctx) => {
    const payload = JSON.stringify({ lifecycle: "wip", session_id: ctx.session?.id ?? "default" });
    const res = run(["hook", "stop", "--format", "json"], payload);
    if (res.code !== 0) {
      // Surface blocking trace failures; the harness should treat this as a
      // completion gate. Set lifecycle per your policy (e.g. "merge").
      pi.log(`trace verify: ${res.out}`);
      return { block: true, reason: "trace verification has blocking failures" };
    }
  });
}
```

## Notes

- The YAML and TypeScript hooks assume `trace` is reachable via `uv run` from
  the repository root. For global setup, install TraceLayer as a tool
  (`uv tool install tracelayer`) and call `trace` directly.
- Block-once semantics are stored in `.trace/cache/session/` (git-ignored);
  `trace context <id>` records context-load acknowledgement per session.
- The `Stop` gate is authoritative: it runs the same `trace verify` policy
  evaluation as CI, so hook coverage gaps cannot silently weaken enforcement.
