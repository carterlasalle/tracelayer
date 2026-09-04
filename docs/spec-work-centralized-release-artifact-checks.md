# Centralized release artifact checks

<!-- trace:v1 id=doc.centralized-release-artifact-checks -->

<!-- trace:exempt reason=document-structure -->
## Goal

Follow-up: a trace release check command that inspects built wheels/sdists for must-include content and must-exclude junk plus a clean-install smoke test, per spec section 60.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-distribution-inspector — Distribution inspector

<!-- trace:v1 id=REQ-distribution-inspector type=requirement work=WORK-centralized-release-artifact-checks -->

Deterministic wheel/sdist inspection enforces the must-include and must-exclude lists from the spec with unit tests on synthetic archives.

### REQ-release-check-command — Release check command

<!-- trace:v1 id=REQ-release-check-command type=requirement work=WORK-centralized-release-artifact-checks -->

trace release check validates the pyproject packaging declaration and optionally builds then inspects and smoke-installs the artifacts.
