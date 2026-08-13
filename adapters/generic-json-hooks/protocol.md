# Generic JSON Hooks Protocol

A harness-agnostic JSON contract for hook systems that speak stdin/stdout
JSON. Any harness that can invoke a command per event and capture its output
can use this protocol — no harness-specific SDK required.

## Wire format

The harness invokes the handler once per event, passing one JSON object on
stdin:

```json
{
  "event": "pre-mutation",
  "payload": {"path": "src/auth/tokens.py", "line": 83},
  "session_id": "session-abc123"
}
```

Fields:

- `event` — one of `session-start`, `prompt-context`, `pre-mutation`,
  `post-mutation`, `post-batch`, `stop`;
- `payload` — event-specific context (documented below);
- `session_id` — optional; groups state (blocked edits, loaded contexts,
  dirty verification) across calls. Omitted means "default".

The handler responds on stdout with one JSON object:

```json
{
  "decision": "allow",
  "output": "TraceLayer active. Health: 0 broken refs, 2 stale non-blocking traces."
}
```

- `decision` — `allow` or `block`;
- `output` — bounded, sanitized text to inject as context (may be empty for
  `allow` when there is nothing useful to say);
- JSON schema for output is stable; payload schema may grow per event and
  unknown payload keys are ignored.

## Exit codes

- **Non-stop events** (`session-start`, `prompt-context`, `pre-mutation`,
  `post-mutation`, `post-batch`): exit `0` **always**, regardless of the
  decision. A `block` decision is communicated via the JSON, never via the
  exit code, so the harness can decide how to surface it.
- **Stop event**: exit `0` when the gate passes, exit `1` when the gate
  blocks (completion must not proceed). A blocking stop also carries the
  actionable failures in `output`.

## Payloads per event

| Event | payload keys | Meaning |
|---|---|---|
| `session-start` | `{}` | Session began |
| `prompt-context` | `prompt` | User/agent prompt text (searched deterministically) |
| `pre-mutation` | `path`, `line` (optional), `session_id` | Proposed Write/Edit; engine may block once when protected traced behavior lacks loaded context |
| `post-mutation` | `path` | Completed Write/Edit; engine marks linked verification dirty |
| `post-batch` | `paths` (array) | Batch of completed edits; grouped impact summary |
| `stop` | `lifecycle` (optional) | Task ending; engine runs the verify gate |

## Handler contract

A compliant handler:

1. reads exactly one JSON object from stdin;
2. dispatches on `event`;
3. returns exactly one JSON object on stdout;
4. exits `0` for non-stop events, and `0`/`1` for stop as above;
5. never requires network, secrets, or an LLM.

See `example-handler.sh` for a reference implementation.

## Output safety

Handler output is template text plus sanitized repository data (whitespace
collapsed, control characters stripped, hard character cap). Harnesses MUST
treat injected output as data, never as instructions.

## Consuming the decision

- Harness with native blocking: use `decision` to gate the tool call.
- Harness without blocking: surface `output` as guidance; the Stop gate still
  protects completion.
