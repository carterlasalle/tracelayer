# Documentation artifact system and useful context engine

<!-- trace:v1 id=doc.documentation-artifact-system-and-useful-context-engine -->

<!-- trace:exempt reason=document-structure -->
## Goal

Phase 3 of knowledge-first ambient development: an artifact template registry with structural validation for SPEC/RFC/ADR/PLAN/RUNBOOK docs, and a trace context engine that briefs tasks, questions, decisions, implementation state, and nearby comments/docstrings.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-artifact-template-registry — Artifact template registry

<!-- trace:v1 id=REQ-artifact-template-registry type=requirement work=WORK-documentation-artifact-system-and-useful-context-engine -->

A registry defines recommended sections, required semantics, lifecycle states, and typical relationships for WORK/TASK/QUESTION/DECISION/REQUIREMENT/SPEC/RFC/ADR/PLAN/RUNBOOK/GUIDE/REFERENCE/MIGRATION/INCIDENT, with deterministic structural validation.

### REQ-engineering-briefing-context — Engineering briefing context

<!-- trace:v1 id=REQ-engineering-briefing-context type=requirement work=WORK-documentation-artifact-system-and-useful-context-engine -->

trace context shows work/tasks/open questions/blockers, implementation state, documentation links, and nearby comments/docstrings/source excerpts.
