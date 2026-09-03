"""Beads detection: optional enhancement, never a dependency (spec Sections 4-5, 74).

TraceLayer natively provides work, tasks, dependencies, questions, decisions,
and ready/blocked state. When Beads is installed AND initialized for the
repository, integrations may enhance queues and coordination on top.
Detection only reads; TraceLayer never initializes Beads on its own.
"""

from __future__ import annotations

import shutil
from pathlib import Path


# trace:v1 id=impl.beads.detect work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status satisfies=REQ-beads-optional-detection
def detect_beads(root: Path | str, enabled: str = "auto") -> dict:
    """Beads availability for a repository (spec Section 5 result shape)."""
    root = Path(root)
    available = shutil.which("bd") is not None
    initialized = (root / ".beads").is_dir()
    if str(enabled).lower() == "false":
        active = False
    elif str(enabled).lower() == "true":
        active = available and initialized
    else:  # auto: first-class integration only when already in use
        active = available and initialized
    return {
        "beads": {
            "available": available,
            "repository_initialized": initialized,
            "active": active,
        }
    }
