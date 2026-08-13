# Concepts

This document explains the core model behind TraceLayer: the three truths,
the trace graph, stable IDs, and staleness. It is the best starting point for
understanding why the system behaves the way it does.

## Three truths

TraceLayer never merges three fundamentally different kinds of information
into a single "traced" checkbox. It tracks them separately because they can
disagree — and that disagreement is exactly what traceability exists to
surface.

### Declared semantic truth (markers)

Created by explicit markers or canonical artifact metadata. A human or agent
states an intended relationship:

```json
{
  "from": "impl.auth.refresh",
  "predicate": "satisfies",
  "to": "REQ-AUTH-017",
  "source_kind": "declared",
  "confidence": 1.0
}
```

This is a *commitment*, not a fact. The implementation may later drift from
the requirement; the marker does not make it true.

### Structural truth (code analysis)

Derived from parsing the repository — function calls, imports, inheritance,
containment:

```json
{
  "from": "impl.auth.middleware",
  "predicate": "calls",
  "to": "impl.auth.refresh",
  "source_kind": "structural",
  "extractor": "tree-sitter-python",
  "confidence": 1.0
}
```

Structural edges describe what the code does, never what it means. A call
does not automatically imply a semantic `depends_on`.

### Observed truth (runtime/CI evidence)

Derived from executed tests, coverage, and CI runs:

```json
{
  "from": "test.auth.refresh-reuse",
  "predicate": "executed",
  "to": "impl.auth.refresh",
  "source_kind": "observed",
  "run_id": "gha-198234",
  "revision": "a81d41f",
  "confidence": 1.0
}
```

Observed truth is bound to a revision and an immutable run. It never gets
edited to look current — a new run is generated instead.

### Why separation matters

A conventional trace matrix can show a requirement as green when a test
exists and passes. TraceLayer can answer each component independently:

```text
Declared test relationship: YES
Test exists: YES
Test passed: YES
Test executed claimed implementation: NO
Requirement revision current: YES
Overall: UNPROVEN
```

The UI/CLI always distinguishes `DECLARED`, `STRUCTURALLY_CONFIRMED`,
`OBSERVED`, `STALE`, `SUGGESTED`, and `UNKNOWN` (NFR-014).

## The trace graph

Nodes are typed artifacts: intent nodes (`goal`, `prd`, `requirement`, `nfr`),
decision/planning nodes (`decision`, `work`, `plan`), realization nodes
(`implementation`, `config`, `operation`, `data`, `prompt`),
verification/documentation nodes (`test`, `document`, `runbook`, `evidence`),
and provenance nodes (`commit`, `pull_request`, `ci_run`, `external`).

Edges are the semantic relationships between them, for example:

| Edge | Meaning | Typical source -> target |
|---|---|---|
| `derived_from` | source was derived from target | requirement -> PRD; plan -> decision |
| `addresses` | source is intended to address target | decision/work -> requirement |
| `satisfies` | source behavior fulfills target contract | implementation -> requirement |
| `implements` | source realizes target plan/decision | implementation/config -> plan/decision |
| `verifies` | source test/evidence verifies target contract | test -> requirement |
| `exercises` | source test intends to execute target implementation | test -> implementation |
| `documents` | source documents target | doc/runbook -> implementation/requirement |
| `deploys` | source ops/config deploys target | operation -> implementation |
| `depends_on` | semantic dependency beyond trivial inferred calls | implementation/requirement -> artifact |
| `supersedes` | source replaces target | decision/requirement -> same type |
| `produces` | source activity produces target | plan/CI -> implementation/evidence |
| `consumes` | source relies on target artifact/data | implementation -> data/config |
| `blocks` | source must resolve before target progresses | work/requirement -> work/release |

The engine additionally derives structural edges (`contains`, `calls`,
`imports`, `inherits`, `references_symbol`, `reads`, `writes`,
`changed_by`, `owned_by`) and observed edges (`executed`, `passed`, `failed`,
`built_in`, `deployed_in`, `attested_by`) with their own `source_kind`, so
semantic commitments are never confused with inference.

A canonical policy path for a standard feature:

```text
WORK -> addresses -> REQUIREMENT
IMPLEMENTATION -> work -> WORK
IMPLEMENTATION -> satisfies -> REQUIREMENT
TEST -> verifies -> REQUIREMENT
TEST -> exercises -> IMPLEMENTATION
EVIDENCE -> proves/observes -> TEST + IMPLEMENTATION + REVISION
```

Policy checks *traversability* of these paths — not the presence of every
field on one line.

## Stable IDs

Every first-class trace node has a stable ID (FR-002). IDs are
human-readable aliases that survive refactors, file moves, and renames:

```text
REQ-AUTH-017
NFR-PERF-004
ADR-0042
WORK-AUTH-237
PLAN-AUTH-237/P3
impl.auth.refresh
impl.geo.offline-resolver
test.auth.refresh-reuse
ops.asr.parakeet-deploy
doc.auth.rotation
prompt.dispatch.classifier
```

Rules:

- Reuse an existing ID when a trace likely exists; never invent a duplicate.
- Move the marker with the behavior; do not rewrite the ID when a symbol
  moves.
- Provenance (commit SHA, line numbers, current paths) is derived by the
  engine, never written into markers — it goes stale the moment it is typed.
- Internally the database maps IDs to opaque `entity_uid` values so that
  renames and history remain clean.

## Fingerprints and staleness

The indexer fingerprints artifacts so that change can be detected
deterministically:

- **Requirement fingerprints** normalize the requirement body (line endings,
  trailing whitespace) while preserving semantic text and acceptance
  criteria — formatting-only edits do not invalidate anything.
- **Implementation fingerprints** store both an exact `source_hash` and an
  AST-normalized `semantic_hash`. Standard policy invalidates evidence on
  semantic hash changes; exact build evidence stays naturally revision-bound.

When an upstream artifact changes, the indexer propagates status:

```text
CURRENT
  |
  | upstream fingerprint changes
  v
STALE_REVIEW_REQUIRED
  |
  | reviewer/agent confirms relationship still valid
  v
REVIEWED_NEEDS_VERIFICATION
  |
  | required evidence produced at current revision
  v
CURRENT
```

If the relationship no longer applies:

```text
STALE_REVIEW_REQUIRED -> RETIRED/REPLACED
```

`supersedes` is temporal, never destructive: `ADR-0042 supersedes ADR-0021`
hides ADR-0021 from default current context, but `--history` still shows it.
Historical evidence remains queryable — it is simply never presented as
current (25.4).
