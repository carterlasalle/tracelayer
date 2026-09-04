# Remind-mode pre-edit coaching when blocking is off

<!-- trace:v1 id=doc.remind-mode-pre-edit-coaching-when-blocking-is-off -->

<!-- trace:exempt reason=document-structure -->
## Goal

When the pre-edit context gate is disabled, the pre-mutation hook currently allows silently. It should emit the coaching briefing as a reminder with decision allow, keeping both gates live with no silent path.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-reminder-mode-briefing — Reminder-mode briefing

<!-- trace:v1 id=REQ-reminder-mode-briefing type=requirement work=WORK-remind-mode-pre-edit-coaching-when-blocking-is-off -->

Non-blocking pre-edit path returns coaching context (purpose, design, status, questions, knowledge, tests) with decision allow instead of empty output.
