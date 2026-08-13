"""Local fixtures for tracelayer.hooks tests (directory scope).

Reuses the shared ``project``/``store`` fixtures from tests/unit/conftest.py
(Project rooted in tmp_path with the graph store at project.db_path — the
same location the stop gate's Engine re-opens).
"""

from __future__ import annotations

import pytest

from tracelayer.hooks.common import HookContext
from tracelayer.hooks.session_state import SessionState


@pytest.fixture
def state(project):
    """A SessionState for the shared project."""
    return SessionState(project)


@pytest.fixture
def ctx(project, store, state):
    """A HookContext bound to the shared store/session."""
    return HookContext(
        project=project, store=store, gitrepo=None, session_id="s1", state=state
    )
