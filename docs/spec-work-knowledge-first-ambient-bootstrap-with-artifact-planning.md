# Knowledge-first ambient bootstrap with artifact planning

<!-- trace:v1 id=doc.knowledge-first-ambient-bootstrap-with-artifact-planning -->

<!-- trace:exempt reason=document-structure -->
## Goal

Phase 2 of knowledge-first ambient development: a deterministic ArtifactPlanningEngine that maps user intent to proportional engineering artifacts (tiny/small/medium/large), real multi-step plan generation in bootstrap, and material-question detection.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-artifact-planning-engine — Artifact planning engine

<!-- trace:v1 id=REQ-artifact-planning-engine type=requirement work=WORK-knowledge-first-ambient-bootstrap-with-artifact-planning -->

A deterministic engine maps intent signals, task kind, and requirement count to an artifact plan (work, requirements, spec depth, rfc, adr, plan, tasks, runbook, docs update) with proportional depth.

### REQ-real-bootstrap-plan-generation — Real bootstrap plan generation

<!-- trace:v1 id=REQ-real-bootstrap-plan-generation type=requirement work=WORK-knowledge-first-ambient-bootstrap-with-artifact-planning -->

Bootstrap derives one plan step per requirement plus verification instead of a single one-line step.

### REQ-question-detection — Question detection

<!-- trace:v1 id=REQ-question-detection type=requirement work=WORK-knowledge-first-ambient-bootstrap-with-artifact-planning -->

The engine surfaces candidate material questions from intent text without auto-creating question nodes for trivia.
