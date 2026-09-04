# Knowledge & Canonical Facts — Full Feature Spec

<!-- trace:v1 id=SPEC-KNOWLEDGE-FACTS type=spec work=WORK-close-adversarial-audit-gaps-on-knowledge-and-facts -->

<!-- trace:exempt reason=document-structure -->
## Summary

Durable engineering knowledge and single-source-of-truth facts, verified
against live artifacts and surfaced in briefings, context, hooks, verify,
and the web UI.

<!-- trace:exempt reason=document-structure -->
## Problem

Engineering memory rots: conventions are rediscovered, failed experiments
repeated, and documentation claims drift from code with no system noticing.

<!-- trace:exempt reason=document-structure -->
## Goals

Knowledge nodes with lifecycle, scoped relevance, templates, and capture
UX. Facts verified against live sources and live dependents. Drift wired
into verify, Stop/CI, coaching, context, and web. Confined source reading.

<!-- trace:exempt reason=document-structure -->
## Non-goals

Automatic mirror rewriting, consumer discovery, and semantic relevance
ranking are explicit follow-ups, not this change.

<!-- trace:exempt reason=document-structure -->
## Architecture

Markers carry intent; the engine materializes nodes; facts.py verifies
live sources against live dependents; TL070 turns drift into verify,
Stop, and CI diagnostics; hooks coach; context and web render.

<!-- trace:exempt reason=document-structure -->
## Data model

Node types finding/learning/anti_pattern/convention/constraint/fact/
value. Marker keys canonical_source/value/selector/scope/severity/
confidence/strength/evidence. Governance and fact edge predicates.

<!-- trace:exempt reason=document-structure -->
## Knowledge lifecycle

ACTIVE UNDER_REVIEW SUPERSEDED INVALIDATED ARCHIVED via state.
Relevance rank: direct, work, requirement, scope. Capped injection.

<!-- trace:exempt reason=document-structure -->
## Fact semantics

Confined adapters for TOML/JSON/YAML/Python/regex/KEY-equals lines.
Selector live reads win; legacy value fallback; neither means
UNVERIFIED. Historical references always CURRENT. Manifest covers
unmarkable files.

<!-- trace:exempt reason=document-structure -->
## Security constraints

canonical_source resolves strictly beneath the repo root,
symlink-aware. Absolute paths, dot-dot traversal, and escapes fail
closed. No policy bypass exists.

<!-- trace:exempt reason=document-structure -->
## Hook behavior

Canonical-source edits get impact briefings; knowledge rides context
briefings; non-blocking mode reminds; post-edit checks verify.

<!-- trace:exempt reason=document-structure -->
## UI behavior

/api/facts and /api/knowledge endpoints with panel views; node detail
carries knowledge, facts, related, and adjacent context.

<!-- trace:exempt reason=document-structure -->
## Ambient capture

knowledge-capture mints IDs, nests body sections with inherit
declarations, and reports missing template sections as guidance.

<!-- trace:exempt reason=document-structure -->
## Acceptance tests

Work-scoped knowledge surfaces for member artifacts. Stale selector
dependents report REVIEW_REQUIRED with observed and expected values.
Escapes are refused. Captured knowledge passes verify immediately.

**Knowledge relevance and metadata.**

Governed by REQ-transitive-knowledge-relevance: knowledge injection
traverses artifact, work, requirement, and scope; metadata fields and
templates exist; capture UX writes clean docs.

**Confined live fact verification.**

Governed by REQ-confined-live-fact-verification: sources confined;
dependents read live; drift blocks via TL070 and surfaces in facts,
context, coaching, and web.
