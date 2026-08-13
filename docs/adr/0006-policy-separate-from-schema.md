# ADR-0006: Policy separate from schema

**Status:** Accepted

## Context

Schema validity and lifecycle readiness are different questions with
different failure modes.

## Decision

The parser validates structure; the policy engine evaluates graph state
against profiles (minimal/standard/strict/safety-critical) and lifecycle
stages (draft/wip/review/merge/release). Waivers are explicit, scoped, and
expiring.

## Consequences

Adoption is incremental; required links increase across lifecycle stages.
