# ADR-0001: Repository-first canonical model

**Status:** Accepted

## Context

Trace identity and software-intent declarations must survive refactors,
harness changes, and external tool churn.

## Decision

The repository is canonical for trace identity, semantic declarations, ADRs,
policy, and the evidence index. External systems (Jira, Linear, Notion,
GitHub) are linked mirrors, never repeated in markers.

## Consequences

Offline operation is possible; external connectors are isolated adapters.
