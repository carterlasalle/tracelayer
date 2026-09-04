# HTML work and question browser panel

<!-- trace:v1 id=doc.html-work-and-question-browser-panel -->

<!-- trace:exempt reason=document-structure -->
## Goal

Follow-up: a work view in the web UI (READY/BLOCKED/OPEN QUESTIONS per work item backed by the ready endpoint) plus workflow links and nearby context in the node detail panel.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-work-view-panel — Work view panel

<!-- trace:v1 id=REQ-work-view-panel type=requirement work=WORK-html-work-and-question-browser-panel -->

The web UI offers a work selector rendering ready/blocked/question groups with click-through to nodes.

### REQ-richer-node-detail — Richer node detail

<!-- trace:v1 id=REQ-richer-node-detail type=requirement work=WORK-html-work-and-question-browser-panel -->

Node detail serves related workflow links and adjacent excerpts so questions visibly resolve into decisions.
