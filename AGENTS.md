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
`skills/traceability/references/marker-protocol.md`) are produced from the
protocol registries; run `uv run trace docs generate` after changing the
protocol and never edit them by hand.


<!-- tracelayer-agent-invariant:v2 -->
This repository uses mandatory semantic traceability. Trace integrity is part of the Definition of Done. Follow the repository traceability skill and any trace instructions injected by hooks. Do not invent trace fields, replace stable IDs during refactors, or remove markers to silence validation. Before completing implementation work, `trace verify --changed` must pass under the active policy.

TRACE AUTHORING IS MANDATORY: whenever a Write/Edit/Create introduces or materially changes a behavioral boundary, that boundary must be trace-accounted in the same change (preserve an existing trace ID, add a canonical `trace:v1` marker, or record an explicit `# trace:exempt` with a reason). Do not write new product behavior first and plan to trace it later — hooks block untraced new behavior before it is written. Example:

    # trace:v1 id=impl.<slug> work=<WORK-ID> satisfies=<REQ-ID>
    def rotate_token(...): ...
