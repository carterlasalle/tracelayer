# ADR-0005: AST symbol attachment over line identity

**Status:** Accepted

## Context

Paths and line numbers break during refactors; trace identity must not.

## Decision

Markers attach to structural symbols via Tree-sitter. Byte/line ranges are
derived metadata, never identity. Ambiguity or detachment is a diagnostic
(TL003), never a silent guess.

## Consequences

Refactor preservation via stable ID + AST containment; unsupported languages
degrade honestly to file-level attachment.
