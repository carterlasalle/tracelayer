# Beads task mirror with completion reconciliation

<!-- trace:v1 id=doc.beads-task-mirror-with-completion-reconciliation -->

<!-- trace:exempt reason=document-structure -->
## Goal

Follow-up: mirror native TraceLayer tasks into Beads with external-ref mapping, mirror dependency edges, and reconcile closed beads against TraceLayer verification (WORK STATE MISMATCH). Native flow never depends on Beads.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-task-mirror-with-mapping — Task mirror with mapping

<!-- trace:v1 id=REQ-task-mirror-with-mapping type=requirement work=WORK-beads-task-mirror-with-completion-reconciliation -->

trace work mirror previews and applies TASK-to-bead creation with TRACE external refs and a persisted mapping; dependencies mirror as bead links.

### REQ-completion-reconciliation — Completion reconciliation

<!-- trace:v1 id=REQ-completion-reconciliation type=requirement work=WORK-beads-task-mirror-with-completion-reconciliation -->

trace work reconcile reports beads closed while TraceLayer verification is incomplete, and question-blocked tasks.
