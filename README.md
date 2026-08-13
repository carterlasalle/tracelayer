# TraceLayer

TraceLayer is an agent-native software traceability system. It makes the
intent, implementation, verification, provenance, and evidence for software
changes traversable as a deterministic graph — so coding agents and humans can
answer "why does this exist?", "what does this change break?", and "is this
actually proven?" without a giant prompt or a hand-maintained trace matrix.

- **Deterministic.** Parsing, indexing, and verification run offline with no
  model calls and no daemon. Same repo + config + evidence in, same verdict
  out (NFR-001, NFR-004).
- **Agent-native.** A small Agent Skill plus event hooks keep agents oriented
  and honest: block-once before editing traced behavior, dirty verification
  after edits, and a Stop gate that refuses to declare completion while
  blocking diagnostics remain.
- **Honest about proof.** Three kinds of truth are kept separate: what is
  *declared* in markers, what is *structural* in the code, and what is
  *observed* in test/CI evidence. A passing test that never executed the
  implementation is reported as UNPROVEN, not green.

## The 5-minute model

Every meaningful artifact is a **node** with a stable trace ID:

```text
WORK-AUTH-237  work item
REQ-AUTH-017   requirement
ADR-0042       decision
PLAN-AUTH-237/P3  plan
impl.auth.refresh  implementation
test.auth.refresh-reuse  test
```

Artifacts declare **edges** next to the behavior they describe, as ordinary
comment markers. The engine derives the rest (path, symbol, line range, git
provenance) — none of that is written by hand:

```python
# trace:v1 id=impl.auth.refresh work=WORK-AUTH-237 satisfies=REQ-AUTH-017
def rotate_refresh_token(token: str) -> TokenPair:
    ...
```

```python
# trace:v1 id=test.auth.refresh-reuse verifies=REQ-AUTH-017 exercises=impl.auth.refresh
def test_reused_refresh_token_is_rejected():
    ...
```

When a requirement changes, the indexer fingerprints it, marks downstream
implementations and tests `STALE_REVIEW_REQUIRED`, and demotes prior evidence
to historical. `trace verify` then refuses to pass at `merge` until someone
reviews or re-verifies. Nothing is claimed wrong — only that the old proof no
longer establishes conformance.

## Quick start

```bash
# 1. Install
uv sync

# 2. Initialize a repo (writes .trace/trace.toml, .trace/policy.toml,
#    .gitignore entries; never overwrites existing config)
trace init

# 3. Index everything once
trace index --all

# 4. Check the health of the whole repo
trace status

# 5. Verify only what changed against the merge lifecycle
trace verify --changed --lifecycle merge

# 6. When a task touches traced behavior, orient first
trace context impl.auth.refresh
```

Then, before declaring any task complete: run the linked tests, ingest
evidence, and run `trace verify --changed` again.

## Documentation

| Topic | Doc |
|---|---|
| Concepts: three truths, graph, stable IDs, staleness | [docs/concepts.md](docs/concepts.md) |
| Marker syntax (generated, normative) | [docs/marker-protocol.md](docs/marker-protocol.md) |
| Edge semantics (generated) | [docs/relationships.md](docs/relationships.md) |
| Policy: profiles, lifecycle, waivers, rule IDs | [docs/policy.md](docs/policy.md) |
| Hook architecture | [docs/hooks.md](docs/hooks.md) |
| Claude Code integration | [docs/claude-code.md](docs/claude-code.md) |
| Test/coverage evidence and proof levels | [docs/evidence.md](docs/evidence.md) |
| CodeOps migration | [docs/migration-codeops.md](docs/migration-codeops.md) |
| Security and threat model | [docs/security.md](docs/security.md) |
| Large repos and monorepos | [docs/large-repos.md](docs/large-repos.md) |
| Agent Skill | [skills/traceability/SKILL.md](skills/traceability/SKILL.md) |

## Adapters

- [Claude Code](adapters/claude-code/README.md)
- [Generic JSON hooks](adapters/generic-json-hooks/protocol.md)
- [OpenCode](adapters/opencode/README.md)

## CI

`.github/workflows/trace.yml` runs deterministic trace validation, test +
coverage evidence ingestion, and evidence-aware verification on every PR and
push to main. Fork PRs get no write tokens for trace commands.
