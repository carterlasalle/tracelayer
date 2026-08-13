# ADR-0003: One-line marker protocol

**Status:** Accepted

## Context

Markers must be grep-friendly (a single ripgrep match), reviewable in Git, and
versioned.

## Decision

A single-line, versioned marker format with typed keys and quoted-value
escaping. One canonical machine schema; agent prompts never redefine fields.

## Consequences

Richness lives in the graph; duplicate-key and unknown-key cases fail
deterministically.
