"""Tests for tracelayer.hooks.stop_gate (spec 22.9 fail-closed gate)."""

from __future__ import annotations

from tests.unit.conftest import make_edge, make_node
from tracelayer.evidence.models import TestOutcome as EvidenceOutcome
from tracelayer.graph.store import entity_uid
from tracelayer.hooks.common import HookContext
from tracelayer.hooks.stop_gate import handle


def _seed(store):
    store.replace_all(
        [
            make_node("REQ-1", "requirement"),
            make_node("impl.one", "implementation"),
            make_node("test.one", "test"),
        ],
        [
            make_edge(entity_uid("impl.one"), "satisfies", entity_uid("REQ-1")),
            make_edge(entity_uid("test.one"), "verifies", entity_uid("REQ-1")),
        ],
    )


def _add_passing_evidence(store):
    store.add_evidence_run(
        "run-1", None, "pytest", "ci",
        "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "pass", None,
        {"require_revision": False},
    )
    store.add_test_results("run-1", [
        EvidenceOutcome(framework_id="test.one", outcome="pass",
                        test_uid=entity_uid("test.one")),
    ])


def _gate(project, store, state, payload):
    ctx = HookContext(project=project, store=store, gitrepo=None, session_id="s1",
                      state=state)
    return handle(ctx, payload)


def test_clean_state_passes_at_wip(project, store, state):
    _seed(store)
    out = _gate(project, store, state, {"lifecycle": "wip"})
    assert out.decision == "allow"
    assert out.json["status"] == "pass"
    assert out.output == "Trace verify passed under lifecycle wip."


def test_default_lifecycle_is_wip(project, store, state):
    _seed(store)
    out = _gate(project, store, state, {})
    assert out.json["lifecycle"] == "wip"
    assert out.decision == "allow"


def test_blocks_on_stale_state_at_merge(project, store, state):
    _seed(store)
    store.set_node_meta("impl.one", "status", "stale_review_required")
    out = _gate(project, store, state, {"lifecycle": "merge"})
    assert out.decision == "block"
    assert "TL110" in {d["rule"] for d in out.json["diagnostics"]}
    assert out.output.startswith("Task cannot complete yet.")


def test_blocks_without_evidence_at_merge(project, store, state):
    _seed(store)
    out = _gate(project, store, state, {"lifecycle": "merge"})
    assert out.decision == "block"
    assert "TL021" in {d["rule"] for d in out.json["diagnostics"]}


def test_passes_after_review_and_evidence(project, store, state):
    _seed(store)
    store.set_node_meta("impl.one", "status", "stale_review_required")
    assert _gate(project, store, state, {"lifecycle": "merge"}).decision == "block"
    # Review resolves the stale status; evidence ingestion satisfies TL021.
    store.set_node_meta("impl.one", "status", "current")
    _add_passing_evidence(store)
    out = _gate(project, store, state, {"lifecycle": "merge"})
    assert out.decision == "allow"
    assert out.output == "Trace verify passed under lifecycle merge."


def test_failure_output_bounded(project, store, state):
    project.config.hooks.max_context_chars = 100
    _seed(store)
    store.set_node_meta("impl.one", "status", "stale_review_required")
    out = _gate(project, store, state, {"lifecycle": "merge"})
    assert out.decision == "block"
    assert len(out.output) <= 100
