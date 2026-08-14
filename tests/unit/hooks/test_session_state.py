"""Tests for tracelayer.hooks.session_state (file-backed session state)."""

from __future__ import annotations

import json

from tracelayer.hooks.session_state import SessionState


# trace:v1 id=test.dogfood.tests.unit.hooks.test_session_state.py type=test
def test_session_id_from_payload(state):
    assert state.session_id_from({"session_id": "abc"}) == "abc"


def test_session_id_from_env(monkeypatch, state):
    monkeypatch.setenv("TRACE_SESSION", "env-session")
    assert state.session_id_from({}) == "env-session"
    assert state.session_id_from({"session_id": "payload-wins"}) == "payload-wins"


def test_session_id_default(state):
    assert state.session_id_from({}) == "default"
    assert state.session_id_from({"session_id": ""}) == "default"
    assert state.session_id_from({"session_id": None}) == "default"


def test_hostile_session_id_stays_in_session_dir(state):
    sid = "../../etc/passwd"
    state.record_context_load(sid, "REQ-1")
    assert (state.session_dir / "etc-passwd.json").exists()
    # No JSON escaped into the cache dir or the project root.
    cache_root = state.session_dir.parent
    escaped = [p for p in cache_root.glob("*.json")]
    assert escaped == []


def test_dotdot_session_id_falls_back_to_default(state):
    state.record_context_load("..", "REQ-1")
    assert (state.session_dir / "default.json").exists()
    # ".." is never materialized as a directory or file.
    assert not (state.session_dir / "..json").exists()
    assert not (state.session_dir.parent / "..json").exists()


def test_context_load_persists_across_instances(project):
    first = SessionState(project)
    first.record_context_load("s1", "impl.one")
    first.record_context_load("s1", "REQ-2")
    second = SessionState(project)
    assert second.context_loaded("s1", "impl.one")
    assert second.context_loaded("s1", "REQ-2")
    assert not second.context_loaded("s1", "impl.other")


def test_blocked_edit_recorded(state):
    assert not state.blocked_without_context("s1", "impl.one")
    state.record_blocked_edit("s1", "impl.one")
    assert state.blocked_without_context("s1", "impl.one")
    # Recording twice must not duplicate the entry.
    state.record_blocked_edit("s1", "impl.one")
    assert state.blocked_without_context("s1", "impl.one")


def test_dirty_marking_and_reading(state):
    assert state.dirty("s1") == set()
    state.mark_dirty("s1", {"impl.one", "test.one"})
    assert state.dirty("s1") == {"impl.one", "test.one"}


def test_dirty_union(state):
    state.mark_dirty("s1", {"a", "b"})
    state.mark_dirty("s1", {"b", "c"})
    assert state.dirty("s1") == {"a", "b", "c"}


def test_clear_resets_session(state):
    state.record_context_load("s1", "impl.one")
    state.record_blocked_edit("s1", "impl.one")
    state.mark_dirty("s1", {"impl.one"})
    state.clear("s1")
    assert not state.context_loaded("s1", "impl.one")
    assert not state.blocked_without_context("s1", "impl.one")
    assert state.dirty("s1") == set()
    # The state file still exists (empty slate, not deleted).
    assert (state.session_dir / "s1.json").exists()


def test_atomic_writes_leave_no_temp_files(state):
    for _ in range(5):
        state.mark_dirty("s1", {"impl.one"})
        state.record_context_load("s1", "REQ-1")
        state.record_blocked_edit("s1", "impl.two")
        state.clear("s1")
    leftovers = list(state.session_dir.glob("*.tmp"))
    assert leftovers == []
    payload = json.loads((state.session_dir / "s1.json").read_text(encoding="utf-8"))
    assert payload == {
        "contexts_loaded": [],
        "blocked": [],
        "dirty": [],
        "active_work": None,
        "active_requirement": None,
        "active_plan": None,
        "obligations": [],
    }


def test_corrupt_state_file_degrades_to_empty(project, state):
    state.record_context_load("s1", "impl.one")
    path = state.session_dir / "s1.json"
    path.write_text("not json {{{", encoding="utf-8")
    assert state.dirty("s1") == set()
    assert not state.context_loaded("s1", "impl.one")
    # The next mutation rewrites a valid file.
    state.mark_dirty("s1", {"impl.one"})
    assert state.dirty("s1") == {"impl.one"}
    assert json.loads(path.read_text(encoding="utf-8"))["dirty"] == ["impl.one"]


def test_sessions_are_isolated(state):
    state.record_context_load("s1", "impl.one")
    state.record_context_load("s2", "impl.two")
    state.mark_dirty("s1", {"impl.one"})
    assert state.context_loaded("s1", "impl.one")
    assert not state.context_loaded("s2", "impl.one")
    assert state.context_loaded("s2", "impl.two")
    assert state.dirty("s2") == set()


def test_session_dir_created_on_demand(project):
    import shutil

    shutil.rmtree(project.session_dir, ignore_errors=True)
    state = SessionState(project)
    assert not project.session_dir.exists()
    state.record_context_load("s1", "REQ-1")
    assert project.session_dir.is_dir()
    assert (project.session_dir / "s1.json").is_file()
