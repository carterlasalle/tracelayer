# ADR-0008: Python + uv v1 implementation

**Status:** Accepted

## Context

The core needs fast iteration, SQLite/XML/Git support, mature Tree-sitter
bindings, and easy hook scripting.

## Decision

Python 3.12+ managed exclusively with `uv`. TypeScript adapters, if needed,
use `yarn`. Dependencies stay small (Typer, Pydantic, tree-sitter, PyYAML);
no ORM, no NetworkX, no daemon.

## Consequences

One package exposes both library and CLI; performance-critical modules can
be replaced behind stable interfaces later.
