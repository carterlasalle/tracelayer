# Hooks
<!-- trace:v1 id=doc.tracelayer.hooks -->

Hooks are a core feature, not an optional reminder layer (22). They sit in
the agent's event loop, call deterministic trace commands, and emit either an
allow/block decision, compact context text, or machine JSON — without
invoking an LLM.

## Generic model

The harness adapter receives an event plus tool/task context and calls the
`trace hook` command. The hook handler returns:

- an **allow/block decision** where the harness supports it;
- compact `additionalContext` / system-reminder text (bounded);
- machine JSON diagnostics.

All hooks are deterministic and offline. The exact harness schema is owned by
each adapter (Claude Code, generic JSON, OpenCode); the core protocol is
stable.

## Event list

| Event | Command | Purpose |
|---|---|---|
| `session-start` | `trace hook session-start` | Announce the trace system without flooding context |
| `prompt-context` | `trace hook prompt-context` | Orient the agent to likely existing trace nodes before broad search |
| `pre-mutation` | `trace hook pre-mutation` | Block the first edit of protected traced behavior when context was not loaded |
| `post-mutation` | `trace hook post-mutation` | Detect changed trace nodes, mark linked verification dirty, give next steps |
| `post-batch` | `trace hook post-batch` | One grouped impact summary for a batch of edits |
| `stop` | `trace hook stop` | Run the verify gate; block completion on blocking failures |

### SessionStart

Announces the system in under 400 characters unless health is failing:

```text
TraceLayer active.
Health: 0 broken refs, 2 stale non-blocking traces.
For traced behavior, load `trace context <id>` before mutation.
`trace verify --changed` is required before completion.
```

### UserPromptSubmit / task intake

Deterministic FTS search over the prompt text returns likely existing trace
nodes. If search confidence is poor, nothing is injected rather than noise:

```text
Potential trace context:
- REQ-ASR-021: street-name fidelity
- impl.asr.bias: contextual bias implementation
- test.asr.streets: verification
Inspect these before creating new trace identities.
```

### PreToolUse Write/Edit — the highest-value hook

Algorithm:

```text
receive proposed mutation
  -> identify target file/range if available
  -> map range to structural symbol
  -> check trace nodes attached to symbol/containing boundary
  -> check task/session state: has relevant trace context been loaded?
  -> evaluate pre-mutation policy
```

For protected traced behavior with no context acknowledgement, the hook
blocks the first mutation and returns:

```text
TRACE CONTEXT REQUIRED

You are modifying:
  impl.asr.bias
  apply_context_bias()

Satisfies:
  REQ-ASR-021

Work:
  WORK-ASR-184

Decision:
  ADR-ASR-014

Linked verification:
  test.asr.streets
  test.asr.homophones

Before editing:
1. Run `trace context impl.asr.bias`.
2. Confirm the intended behavior still satisfies REQ-ASR-021.
3. Preserve the stable trace ID through refactors.
4. Re-run linked verification after editing.

Then retry the edit.
```

The context acknowledgement is stored in ephemeral session state keyed by
task/session + trace ID, so the block fires once per session and disappears
after `trace context <id>` runs.

### New file / boundary creation
<!-- trace:v1 id=doc.tracelayer.hooks.authoring work=WORK-TL-001 -->

New files are **not** "reminded with judgment" anymore: the authoring gate
simulates the proposed Write/Edit, extracts every behavioral boundary,
classifies each NEW / MODIFIED / UNCHANGED by qualified identity, and
**blocks until every meaningful boundary is locally trace-accounted** with
the full authoring plan (one mutation -> all boundaries listed with their
exact markers, all persisted as durable obligations). Trivial code is
exempted explicitly (`# trace:exempt reason=<why>`); imports, boilerplate,
and generated code never need traces. Opaque mutations (Bash, generators,
formatters) receive the same treatment through a post-mutation
working-tree scan that creates obligations for every untraced boundary it
finds.


### PostToolUse Write/Edit

After a successful mutation: re-index the changed file, compute which trace
nodes changed structurally/semantically, mark verification dirty, and emit
next steps:

```text
TRACE CHANGE DETECTED

Changed: impl.asr.bias
Requirement: REQ-ASR-021
Semantic hash changed: yes

Required verification now dirty:
- test.asr.streets
- test.asr.homophones

Run linked verification, then `trace verify --changed`.
```

### PostToolBatch

One grouped injection per batch instead of one message per edit:

```text
TRACE IMPACT OF EDIT BATCH
Changed: impl.auth.refresh, impl.auth.store, test.auth.refresh-reuse
Affected requirements: REQ-AUTH-017, REQ-AUTH-019
Remaining required verification: test.auth.expired
```

### Stop gate

Internally runs `trace verify --changed --lifecycle <requested> --json` and
blocks completion when blocking failures exist, injecting only actionable
failures:

```text
Task cannot complete yet.

impl.asr.bias changed.
Declared test test.asr.streets passed, but current evidence does not prove it executed the changed implementation.

Required:
1. Run the linked verification with per-test coverage enabled.
2. Re-run `trace verify --changed`.
```

## Block-once semantics

The pre-mutation hook blocks **once** per session for a given trace node when
all of these hold:

- the target is protected (has `satisfies`/`work` semantic edges);
- `pre_edit_require_context` is enabled in config;
- the relevant trace context has not been loaded in this session;
- `pre_edit_block_once` is enabled and the node was not already blocked.

After the agent runs `trace context <id>` (which records the load), the retry
is allowed. This prevents both unchecked edits and a nagging loop.

## Safety

Hook text is system-generated from templates and sanitized trace metadata
(22.10). Rules:

- all injected repository text passes through bounded sanitization
  (`sanitize_text`: whitespace collapsed, control characters stripped, hard
  character cap, `repository data:` prefix);
- hook output is template text, never raw requirement bodies;
- hooks never concatenate arbitrary trace text into command strings (T1);
- injections normally stay below 1,500 characters with a configurable hard
  cap (`max_context_chars`); deeper information is retrieved on demand via
  `trace context` (NFR-006).

## Adapters

- [Claude Code](../adapters/claude-code/README.md) — `settings.template.json`
  wired to `trace hook <event> --format claude`.
- [Generic JSON hooks](../adapters/generic-json-hooks/protocol.md) — stdin
  `{event, payload, session_id}`, stdout `{decision, output}`.
- [OpenCode](../adapters/opencode/README.md) — template config mapping the
  same commands.
- [Pi](../adapters/pi/README.md) — Claude-Code-compatible command hooks
  (PreToolUse deny/allow contract) for the Pi coding agent.
- [Oh My Pi](../adapters/oh-my-pi/README.md) — YAML hooks template
  (`pi-yaml-hooks`) plus a native TypeScript gate for the block-once
  pre-mutation guard and fail-closed stop gate.
- [Codex CLI](../adapters/codex/README.md) — `hooks.json` with `codex_hooks`
  enabled; PreToolUse deny-only and PostToolUse observe-only (Codex upstream
  limitations documented).
- [Hermes](../adapters/hermes/README.md) — shell hooks
  (`pre_tool_call`/`post_tool_call`) with exit-2 blocking and consent model.
