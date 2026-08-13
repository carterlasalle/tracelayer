# Contributing to TraceLayer

TraceLayer is a deterministic traceability engine that sits in the coding-agent control loop. Small changes can alter marker parsing, staleness propagation, policy gates, or hook enforcement for every repository that adopts it. Contributions are welcome when they preserve the protocol invariants and include evidence for the behavior they change.

Read [AGENTS.md](AGENTS.md) for repository-specific engineering rules. The normative specification is [traceability-system-master-spec.md](traceability-system-master-spec.md); generated documentation must come from the registries, never be hand-edited.

## Development setup

### Prerequisites

- Git
- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/) — dependency management is uv-only

```bash
git clone https://github.com/carterlasalle/tracelayer.git
cd tracelayer
uv sync
```

Confirm the baseline before editing:

```bash
uv run pytest
uv run ruff check .
uv run trace docs generate --check
```

## Repository rules

- **Deterministic before semantic.** If a fact can be proven by parsing, Git, file existence, tests, or coverage, the engine must prove it. Never spend an LLM on it, and never hand an LLM the job of the deterministic engine.
- **One canonical schema.** The marker grammar, ontology, and TL-rule registries in `src/tracelayer/protocol/` and `src/tracelayer/diagnostics.py` are the single source of truth. `docs/marker-protocol.md`, `docs/relationships.md`, and `skills/traceability/references/marker-protocol.md` are generated — run `uv run trace docs generate` after changing the protocol and never edit them by hand.
- **Every failure is explainable.** Diagnostics come from the rule registry (`tracelayer.diagnostics.make`) with severity and remediation. Recoverable input problems are diagnostics, never exceptions.
- **Trace integrity is part of the Definition of Done.** Before completing implementation work, `trace verify --changed` must pass under the active policy.
- **Derived facts are never declared.** Paths, line numbers, commit SHAs, test results, and structural/observed edges must not appear in markers.
- **Ambiguity fails loudly.** A parser that cannot decide reports TL003; it never attaches a marker to a guessed symbol.

## Pull requests

- Protected, squash-only pull requests with required checks.
- Every PR must include tests for the behavior it changes and update generated reference docs when the protocol changes.
- Use the [pull request template](.github/pull_request_template.md): list affected trace IDs, summarize the change, and check the verification boxes.
- New observable contracts (CLI flags, JSON shapes, rule behavior) get a focused test — see `tests/integration/test_dod_gaps.py` for the pattern.

## Development commands

```bash
uv run pytest                      # full suite (709 tests)
uv run pytest tests/unit/protocol -q   # scoped runs work too
uv run ruff check .                # lint; fix with uv run ruff check --fix .
uv run trace --help                # CLI smoke
uv run trace docs generate --check # protocol docs drift check
```

## Testing conventions

- Deterministic only: `tmp_path` fixtures, no network, no wall-clock dependence. Normalize timestamps and SHAs in golden outputs.
- Shared fixtures live in `tests/conftest.py` (`make_git_repo`, `run_trace`).
- Property tests use Hypothesis (`tests/unit/protocol/test_property.py`).
- Hook behavior is tested by simulating harness events as JSON; no harness is required.
- The repository traces itself: add `trace:v1` markers to meaningful behavior boundaries you create, then run `uv run trace verify --changed`.
