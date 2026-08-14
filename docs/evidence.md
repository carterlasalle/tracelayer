# Evidence
<!-- trace:v1 id=doc.tracelayer.evidence -->

TraceLayer treats evidence as immutable observation. Evidence records answer
one question precisely: *at this revision, did this test run, and did it
execute this implementation?* Old records are never edited to look current —
a new run is generated (25.1).

## Proof levels

Coverage proof is graded L0–L3. Policy can require different levels for
different profiles/lifecycles.

### Level 0 — no execution proof

Only a declaration:

```text
test -> exercises -> implementation
```

The test exists and is declared to exercise the implementation, but nothing
proves it ran against it.

### Level 1 — suite-level implementation execution

Coverage proves the implementation ran during the suite, but not which
specific test caused it. A suite-level coverage report that intersects the
implementation's line range yields L1.

### Level 2 — test-scoped execution

Evidence proves the specific traced test executed the traced implementation
(per-test coverage, e.g. `coverage` collected with `--cov-context=test`).

### Level 3 — richer behavioral evidence

Optional instrumentation proves a specific branch, condition, path, or
property tied to acceptance criteria.

The proof level is surfaced in `trace context`:

```text
Verification:
  test.auth.refresh-reuse  PASS  EXECUTION=L2  CURRENT
  test.auth.expired        PASS  EXECUTION=L2  CURRENT
```

## Input formats

### JUnit XML

`trace evidence ingest --junit junit.xml` parses standard JUnit XML
(`<testsuite>` and nested `<testsuites>`) with stdlib `xml.etree.ElementTree`.
Test cases with `<failure>`, `<error>`, or `<skipped>` children map to
`fail`/`error`/`skip` outcomes. Malformed XML raises a parse error that the
ingest path converts into a TL051 diagnostic — XML is treated as untrusted
input.

### Cobertura coverage

`trace evidence ingest --coverage coverage.xml` parses Cobertura
`<packages>/<classes>` structure into per-file hit lines. Suite-level
execution edges are derived by intersecting hit lines with traced
implementation line ranges.

### Normalized evidence

A JSON record with `"schema": "tracelayer-evidence/v1"`:

```json
{
  "schema": "tracelayer-evidence/v1",
  "run_id": "gha-198234",
  "revision": "a81d41f",
  "provider": "github-actions",
  "workflow": "ci",
  "started_at": "2026-08-09T09:10:00Z",
  "completed_at": "2026-08-09T09:14:12Z",
  "status": "pass",
  "tests": [
    {
      "trace_id": "test.auth.refresh-reuse",
      "framework_id": "tests/auth/test_refresh.py::test_reuse",
      "outcome": "pass"
    }
  ]
}
```

`--normalized evidence.json` ingests the full record, including per-test
execution edges (`coverage_kind="per_test"` → proof L2).

## Freshness

Evidence is current only if (25.3):

- its revision matches the relevant evaluated revision, or policy allows
  ancestor evidence whose semantic fingerprint is unchanged;
- the target requirement fingerprint matches the reviewed/verified
  fingerprint;
- the implementation semantic fingerprint matches the evidence binding;
- required tests passed.

Revision mismatches produce TL050 (or TL062 under safety-critical
"exact revision" policy). A requirement or implementation whose semantic
hash changed invalidates old evidence — the run stays queryable as
historical but is never presented as current (25.4).

## Ingesting in CI

The reference workflow (`.github/workflows/trace.yml`) runs:

```bash
./scripts/test-with-trace-evidence.sh              # pytest + coverage artifacts
uv run trace evidence ingest --junit junit.xml --coverage coverage.xml --revision "$GITHUB_SHA"
uv run trace verify --changed --lifecycle merge --require-evidence
```

`--require-evidence` forces evidence-dependent rules (TL022) even below
their profile gate.

## Anti-patterns

- Manually authored `test_passed=true` marker fields are not supported — that
  is fabricated evidence (T6).
- A passing test with no execution edge is L0, and policy says so.
- Editing an old evidence record to match a new revision is prohibited;
  ingest a new run.
