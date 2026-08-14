"""Tests for tracelayer.hooks.post_batch (spec 22.6 grouped summary)."""

from __future__ import annotations

from tests.unit.conftest import make_edge, make_node
from tracelayer.graph.store import entity_uid
from tracelayer.hooks.post_batch import handle


# trace:v1 id=test.dogfood.tests.unit.hooks.test_post_batch.py type=test
def _seed(store):
    store.replace_all(
        [
            make_node("impl.one", "implementation", path="src/auth.py"),
            make_node("REQ-1", "requirement"),
            make_node("test.one", "test"),
        ],
        [
            make_edge(entity_uid("impl.one"), "satisfies", entity_uid("REQ-1")),
            make_edge(entity_uid("test.one"), "verifies", entity_uid("REQ-1")),
        ],
    )


def test_grouped_summary_from_state(ctx):
    _seed(ctx.store)
    ctx.state.mark_dirty("s1", {"impl.one", "test.one"})
    out = handle(ctx, {"paths": ["src/auth.py", "src/other.py"]})
    assert out.decision == "allow"
    assert out.output.startswith("TRACE IMPACT OF EDIT BATCH")
    assert "Changed: impl.one" in out.output
    assert "Affected requirements: REQ-1" in out.output
    assert "Remaining required verification: impl.one, test.one" in out.output


def test_empty_when_no_dirty_or_paths(ctx):
    _seed(ctx.store)
    out = handle(ctx, {"paths": ["src/auth.py"]})
    assert out.decision == "allow"
    assert out.output == ""
    out = handle(ctx, {"paths": []})
    assert out.output == ""


def test_output_bounded(ctx):
    _seed(ctx.store)
    ctx.state.mark_dirty("s1", {"impl.one", "test.one"})
    ctx.project.config.hooks.max_context_chars = 60
    out = handle(ctx, {"paths": ["src/auth.py"]})
    assert len(out.output) <= 60
