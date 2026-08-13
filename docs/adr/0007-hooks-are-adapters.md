# ADR-0007: Hooks are adapters; core remains harness-agnostic

**Status:** Accepted

## Context

Harness hook schemas (Claude Code, OpenCode, ...) evolve independently of
the trace protocol.

## Decision

The core exposes deterministic hook-event commands returning bounded,
sanitized text and JSON. Harness-specific serialization lives in adapters
and is verified at release time.

## Consequences

The core never shells out to harness code; hook text treats repository data
as data (prompt-injection safe).
