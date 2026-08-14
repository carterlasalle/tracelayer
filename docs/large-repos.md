# Large Repositories and Monorepos
<!-- trace:v1 id=doc.tracelayer.large.repos -->

TraceLayer is built to scale to large repositories without a mandatory
daemon, vector database, or graph server (NFR-011). This document covers
incremental indexing, monorepo scopes, and performance targets.

## Incremental indexing

A full index is a one-time or `--clean` operation:

```bash
trace index --all
```

Daily work uses the incremental path:

```bash
trace index --changed
```

`index --changed` uses Git diff plus cached file fingerprints (NFR-005) and
must identify:

- modified files;
- renamed files;
- deleted files;
- potentially affected upstream/downstream artifacts.

It reparses changed files plus a bounded dependency closure required for
consistency — never the whole repository when cache state is valid. Cache
validity is fingerprint-checked, so stale caches are detected rather than
trusted (T7).

## Monorepo scopes

Monorepo support is first-class (NFR-013). Configuration defines package or
service scopes:

```toml
[scopes]
auth = ["src/auth/**", "tests/auth/**"]
billing = ["src/billing/**", "tests/billing/**"]
ops = ["ops/**", "config/**"]
```

Rules:

- every file belongs to the longest matching scope prefix; unmatched files
  are root scope;
- scopes do not fragment the graph — cross-scope trace edges are preserved
  and queryable;
- verification can be scoped to the changed packages, but requirement
  ancestry and downstream impact are evaluated across scope boundaries;
- `[discovery]` include/exclude globs and `generated = [...]` patterns
  control what is scanned (generated files are exempt from mandatory marker
  rules by default; the generator or source template may be traced instead).

## Performance targets and safeguards

- **Offline baseline**: parsing, indexing, querying, verification, hooks, and
  local evidence analysis run offline with no daemon (NFR-001, NFR-002).
- **Bounded work**: maximum file size (2 MiB) and marker-count safeguards;
  traversal defaults are bounded and deep expansion requires explicit
  `--depth` (T9).
- **FTS search**: nodes are indexed in SQLite FTS5 (trace IDs, titles,
  symbol names, summaries, requirement excerpts, work labels) — whole files
  are never indexed. Token and ID queries both resolve.
- **Incremental reparse**: changed-file verification avoids full-repository
  reparse where cache state is valid.
- **Clean CI**: CI builds from a clean cache by default until cache
  integrity is mature.

## Adoption ladder for large repos

Start in observe-only mode and grow scope deliberately (spec 34):

1. **Stage 0** — `trace init --observe`; index the existing repo; no markers
   required. `trace status` reports health without enforcement.
2. **Stage 1** — trace new work only; legacy code is not forced into
   compliance.
3. **Stage 2** — trace touched legacy behavior when materially modified.
4. **Stage 3** — standard gate on protected branches (broken/stale required
   traces block).
5. **Stage 4** — evidence-aware verification (test results + coverage).
6. **Stage 5** — semantic audit / strict profile for high-risk changes.

A system that demands complete historical traceability on day one will be
abandoned; the ladder exists precisely to prevent that.
