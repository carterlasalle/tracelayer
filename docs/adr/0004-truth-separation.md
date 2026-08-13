# ADR-0004: Declared/structural/observed truth separation

**Status:** Accepted

## Context

A declared test link is a claim; coverage is proof. Confusing them creates
false green matrices.

## Decision

Every edge carries `source_kind` (`declared | structural | observed |
imported | suggested`) and confidence. Declared `exercises` is never
displayed as runtime-confirmed without an observed `executed` edge.

## Consequences

Proof levels (L0-L3) are explicit; UI must distinguish claim from proof.
