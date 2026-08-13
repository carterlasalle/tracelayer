"""Tests for tracelayer.hooks.session_start (spec 22.1 health announcement)."""

from __future__ import annotations

from tests.unit.conftest import make_node
from tracelayer.diagnostics import make
from tracelayer.hooks.session_start import handle


def test_healthy_summary_within_400_chars(ctx):
    ctx.store.replace_all([make_node("REQ-1", "requirement")], [])
    out = handle(ctx, {})
    assert out.decision == "allow"
    assert out.output.startswith("TraceLayer active.")
    assert len(out.output) <= 400
    assert out.json["health"] == {
        "broken_refs": 0,
        "stale": 0,
        "policy": "standard",
        "lifecycle": "wip",
    }


def test_reports_broken_refs_and_stale(ctx):
    ctx.store.replace_all(
        [
            make_node("REQ-1", "requirement"),
            make_node("impl.stale", "implementation", status="stale_review_required"),
        ],
        [],
    )
    ctx.store.insert_diagnostics(
        [make("TL002", message="broken edge", trace_id="REQ-1")]
    )
    out = handle(ctx, {})
    assert "Health: 1 broken refs, 1 stale traces." in out.output
    assert out.json["health"]["broken_refs"] == 1
    assert out.json["health"]["stale"] == 1
    # The review line names the stale trace.
    assert "Review:" in out.output and "impl.stale" in out.output


def test_output_bounded_when_unhealthy(ctx):
    ctx.project.config.hooks.max_context_chars = 100
    ctx.store.replace_all(
        [make_node("impl.stale", "implementation", status="stale_review_required")],
        [],
    )
    out = handle(ctx, {})
    assert len(out.output) <= 100
