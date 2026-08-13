"""Tests for tracelayer.hooks.post_mutation (spec 22.5 dirty tracking)."""

from __future__ import annotations

from tests.unit.conftest import make_edge, make_node
from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint
from tracelayer.graph.store import entity_uid
from tracelayer.hooks.common import HookContext
from tracelayer.hooks.post_mutation import handle

ORIG = 'def login():\n    # trace:v1 id=impl.one type=implementation title="Login"\n    return "v1"\n'
FP = semantic_fingerprint(normalize_block(ORIG))


def _seed(store):
    store.replace_all(
        [
            make_node("impl.one", "implementation", path="src/auth.py", start=1,
                      end=3, fingerprint=FP),
            make_node("REQ-1", "requirement"),
            make_node("test.one", "test"),
        ],
        [
            make_edge(entity_uid("impl.one"), "satisfies", entity_uid("REQ-1")),
            make_edge(entity_uid("test.one"), "verifies", entity_uid("REQ-1")),
        ],
    )


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_unchanged_file_not_marked(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/auth.py", ORIG)
    out = handle(ctx, {"path": "src/auth.py"})
    assert out.decision == "allow"
    assert out.json["changed"] == []
    assert out.json["dirty"] == []
    assert out.json["output"] == ""
    assert ctx.state.dirty("s1") == set()


def test_symbol_change_marks_dirty(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/auth.py", ORIG)
    handle(ctx, {"path": "src/auth.py"})
    changed = 'def login():\n    # trace:v1 id=impl.one type=implementation title="Login"\n    return "v2"\n'
    _write(ctx.project.root, "src/auth.py", changed)
    out = handle(ctx, {"path": "src/auth.py"})
    assert out.json["changed"] == ["impl.one"]
    assert set(out.json["dirty"]) == {"impl.one", "test.one"}
    # Dirty state persists in session state.
    assert ctx.state.dirty("s1") == {"impl.one", "test.one"}


def test_guidance_lists_requirement_and_tests(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/auth.py", ORIG)
    changed = 'def login():\n    # trace:v1 id=impl.one type=implementation title="Login"\n    return "v3"\n'
    _write(ctx.project.root, "src/auth.py", changed)
    out = handle(ctx, {"path": "src/auth.py"})
    assert "TRACE CHANGE DETECTED" in out.json["output"]
    assert "Changed: impl.one" in out.json["output"]
    assert "Requirement: REQ-1" in out.json["output"]
    assert "Required verification now dirty:" in out.json["output"]
    assert "- test.one" in out.json["output"]


def test_marker_removed_marks_changed(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/auth.py", ORIG)
    handle(ctx, {"path": "src/auth.py"})
    _write(ctx.project.root, "src/auth.py", 'def login():\n    return "v3"\n')
    out = handle(ctx, {"path": "src/auth.py"})
    assert out.json["changed"] == ["impl.one"]
    assert set(out.json["dirty"]) == {"impl.one", "test.one"}


def test_new_file_no_forced_marker(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/newmod.py",
           '# trace:v1 id=impl.new type=implementation title="New"\ndef n(): pass\n')
    out = handle(ctx, {"path": "src/newmod.py"})
    assert out.decision == "allow"
    assert out.json["changed"] == []
    assert out.json["dirty"] == []
    assert out.json["output"] == ""
    assert ctx.state.dirty("s1") == set()


def test_missing_file_allows(ctx):
    _seed(ctx.store)
    out = handle(ctx, {"path": "src/ghost.py"})
    assert out.decision == "allow"
    assert out.json["changed"] == []


def test_no_store_allows(project, state):
    ctx = HookContext(project=project, store=None, gitrepo=None, session_id="s1",
                      state=state)
    out = handle(ctx, {"path": "src/auth.py"})
    assert out.decision == "allow"
    assert out.json["changed"] == []
    assert out.json["output"] == ""


def test_dirty_persists_in_new_session_state(ctx):
    _seed(ctx.store)
    _write(ctx.project.root, "src/auth.py", ORIG)
    changed = 'def login():\n    # trace:v1 id=impl.one type=implementation title="Login"\n    return "v4"\n'
    _write(ctx.project.root, "src/auth.py", changed)
    handle(ctx, {"path": "src/auth.py"})
    assert ctx.state.dirty("s1") == {"impl.one", "test.one"}
