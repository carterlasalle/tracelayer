# ADR-0003: One-line trace:v1 protocol

**Status:** Accepted

## Context

Markers must be grep-friendly (`rg 'trace:v1'`), reviewable in Git, and
versioned.

## Decision

A single-line, versioned marker format with typed keys and quoted-value
escaping. One canonical machine schema; agent prompts never redefine fields.

## Consequences

Richness lives in the graph; duplicate-key and unknown-key cases fail
deterministically.
