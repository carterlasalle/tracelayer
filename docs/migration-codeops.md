# CodeOps Migration
<!-- trace:v1 id=doc.tracelayer.migration.codeops -->

TraceLayer can import repositories that use `codeops:trace` annotations. The
importer preserves useful intent without pretending ambiguous fields have
stronger semantics than they do (33.1).

## Workflow

Migration is deliberately a three-step, reviewable process — never one opaque
automatic rewrite (33.3):

```bash
# 1. Scan: find every codeops:trace annotation in the repo
trace migrate codeops --scan

# 2. Plan: produce a reviewable migration plan with classifications
trace migrate codeops --plan migration.json

# 3. Apply: rewrite only the deterministic and high-confidence items
trace migrate codeops --apply migration.json
```

`--apply` supports `--dry-run` to preview the exact per-file changes without
touching anything.

## Input

```text
codeops:trace work_item=ABC-123 spec=specs/billing.md#REQ-17 plan=phase-1/task-2 test=tests/test_export.py::test_headers doc=docs/export.md evidence=.memory-bank/... commit=abc123
```

## Classification

Every item is classified into one of:

- **deterministic** — safe to rewrite automatically;
- **high-confidence** — contextual but reliable;
- **requires_review** — ambiguous; the plan proposes but never auto-applies;
- **dropped/derived** — removed from source (recorded as import metadata).

| Input field | Classification | Result |
|---|---|---|
| `work_item=ABC-123` | deterministic | `work=ABC-123` |
| `spec=` resolving to a requirement ID, attached to an implementation where context clearly means realization | high-confidence | `satisfies=<resolved-req>` |
| `spec=` resolving to a requirement ID, attached to a test | high-confidence | `verifies=<resolved-req>` |
| `spec=` with no clear realization meaning | requires_review | imported `references` diagnostic |
| `plan=` | deterministic | `implements=<plan-id>` (or `plan=` convenience relation) |
| `test=`, `doc=`, `ops=`, `prompt=` | requires_review | proposed first-class nodes/edges (artifact discovery) |
| `commit=` | dropped | recorded as historical import metadata only |
| `jira_ref` / `github_ref` / `notion_ref` | requires_review | consolidated onto the work node |
| blank placeholder fields | dropped | omitted |
| unknown fields (`ops=`, `incident=`, ...) | permissive | kept with diagnostics showing the mapping (33.2) |

Attachment context matters: a marker inside a test file (`tests/`,
`*_test.*`, `test_*`) maps `spec=` to `verifies`; elsewhere it maps to
`satisfies` only when the context clearly means realization.

## Review report

The plan (`tracelayer-migration/v1`) carries per-item notes and per-file
counts so a human or agent can approve `requires_review` items explicitly.
`requires_review` lines are never rewritten by `--apply`.

## Related

- `trace migrate scry --scan` detects `scry:inline` / `scry:artifact`-style
  annotations and reports them for manual review. v1 is detection only — no
  auto-apply.
- `trace doctor` re-detects migration-relevant issues (duplicates, broken
  refs, detached markers) and suggests deterministic cosmetic fixes only.
- Adoption guidance for existing repositories: see spec Section 34
  (Stage 0 observe-only through Stage 5 semantic audit). A system that
  demands full historical traceability on day one gets abandoned.
