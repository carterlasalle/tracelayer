# TraceLayer

Agent-native software traceability system. Python 3.12+ core, managed with `uv`.

This repository uses mandatory semantic traceability. Trace integrity is part
of the Definition of Done. Follow the repository traceability skill
(`skills/traceability/SKILL.md`) and any trace instructions injected by hooks.

Do not invent trace fields, replace stable IDs during refactors, or remove
markers to silence validation. Before completing implementation work,
`trace verify --changed` must pass under the active policy.

Contributor workflow:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run trace --help
```

Generated docs (`docs/marker-protocol.md`, `docs/relationships.md`,
`skills/traceability/marker-protocol.md`) are produced from the protocol
registries; run `uv run trace docs generate` after changing the protocol and
never edit them by hand.
