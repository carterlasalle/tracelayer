# ADR-0002: SQLite materialized index

**Status:** Accepted

## Context

The trace graph must be queryable offline with low latency and zero daemons.

## Decision

SQLite (WAL mode) at `.trace/cache/index.sqlite3` holds a materialized index
rebuildable from the repository plus evidence inputs. No ORM; stdlib
`sqlite3`. Cache is Git-ignored; canonical declarations remain textual.

## Consequences

Deterministic rebuilds are possible; schema migrations must be versioned.
