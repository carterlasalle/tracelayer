# TraceLayer native work/task/question/decision model

<!-- trace:v1 id=doc.trace-layer-native-work-task-question-decision-model -->

<!-- trace:exempt reason=document-structure -->
## Goal

Phase 1 of knowledge-first ambient development: first-class WORK/TASK/QUESTION/DECISION nodes, canonical task and question states, dependency edges, and native ready/blocked computation without Beads.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-native-work-task-question-decision-ontology — Native work/task/question/decision ontology

<!-- trace:v1 id=REQ-native-work-task-question-decision-ontology type=requirement work=WORK-trace-layer-native-work-task-question-decision-model -->

The graph supports WORK, TASK, QUESTION, DECISION, SPEC, RFC, PLAN_STEP node types and work-relationship edges (blocked_by, related_to, discovered_from, answers, answered_by, etc).

### REQ-task-and-question-lifecycle-states — Task and question lifecycle states

<!-- trace:v1 id=REQ-task-and-question-lifecycle-states type=requirement work=WORK-trace-layer-native-work-task-question-decision-model -->

Tasks support TODO/READY/IN_PROGRESS/PARTIALLY_COMPLETE/BLOCKED/WAITING_FOR_DECISION/WAITING_FOR_INPUT/DEFERRED/DONE/CANCELLED/NOT_IMPLEMENTED; questions support OPEN/ANSWERED/SUPERSEDED/NO_LONGER_RELEVANT/DEFERRED.

- acceptance: state normalization covered by unit tests

### REQ-native-ready-state-computation — Native ready-state computation

<!-- trace:v1 id=REQ-native-ready-state-computation type=requirement work=WORK-trace-layer-native-work-task-question-decision-model -->

trace work ready computes READY/BLOCKED from task state, blocking edges, and open questions with no Beads dependency.

- acceptance: trace work ready runs on a fixture repo
