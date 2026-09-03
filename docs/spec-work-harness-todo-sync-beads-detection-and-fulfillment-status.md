# Harness TODO sync, Beads detection, and fulfillment status

<!-- trace:v1 id=doc.harness-todo-sync-beads-detection-and-fulfillment-status -->

<!-- trace:exempt reason=document-structure -->
## Goal

Phase 4 of knowledge-first ambient development: harness TODO adapters that persist Claude/OMP/Codex todos as native TraceLayer tasks, optional Beads detection that never downgrades native functionality, and derived implementation/requirement fulfillment status.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-harness-todo-adapters — Harness TODO adapters

<!-- trace:v1 id=REQ-harness-todo-adapters type=requirement work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status -->

Canonical normalization of harness todo events (created/updated/started/blocked/completed/cancelled) with harness origin preserved, plus plan-doc sync that persists todos as TASK nodes.

### REQ-beads-optional-detection — Beads optional detection

<!-- trace:v1 id=REQ-beads-optional-detection type=requirement work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status -->

TraceLayer detects Beads availability, repo initialization, and active use; native work/task flow never depends on it and Beads is never silently initialized.

### REQ-fulfillment-status — Fulfillment status

<!-- trace:v1 id=REQ-fulfillment-status type=requirement work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status -->

Implementation state and requirement fulfillment (unimplemented/partial/implemented/verified) derive from graph edges and evidence instead of manual flags.
