# Coaching-first hook briefings with token budget

<!-- trace:v1 id=doc.coaching-first-hook-briefings-with-token-budget -->

<!-- trace:exempt reason=document-structure -->
## Goal

Follow-up: rewrite the pre-mutation block text coaching-first (purpose, design, status, open questions, knowledge, tests) while guaranteeing the enforcement action survives max_context_chars truncation, preserving all asserted strings.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-coaching-first-block-text — Coaching-first block text

<!-- trace:v1 id=REQ-coaching-first-block-text type=requirement work=WORK-coaching-first-hook-briefings-with-token-budget -->

The pre-edit briefing leads with usable engineering context and keeps every asserted string and the fail-closed decision.

### REQ-truncation-safe-budget — Truncation-safe budget

<!-- trace:v1 id=REQ-truncation-safe-budget type=requirement work=WORK-coaching-first-hook-briefings-with-token-budget -->

Coaching content is fitted to the budget minus the reserved enforcement tail so the required action is never truncated away.
