"""Normalized evidence schema validation + freshness/proof levels
(contract §E, spec 25.2/25.3)."""

from __future__ import annotations

import json

import pytest

from tests.unit.conftest import make_node
from tracelayer.evidence.freshness import evidence_is_current, proof_level
from tracelayer.evidence.models import (
    EVIDENCE_SCHEMA,
    ExecutionRecord,
)
from tracelayer.evidence.normalized import EvidenceFormatError, parse_normalized

VALID = {
    "schema": EVIDENCE_SCHEMA,
    "run_id": "run-1",
    "revision": "abc123",
    "provider": "pytest",
    "workflow": "ci",
    "started_at": "2026-01-01T00:00:00Z",
    "completed_at": "2026-01-01T00:01:00Z",
    "status": "pass",
    "tests": [{"framework_id": "tests.app.test_a", "outcome": "pass"}],
    "execution_edges": [],
}


def write_norm(tmp_path, data) -> str:
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_parse_normalized_roundtrip(tmp_path):
    ev = parse_normalized(__import__("pathlib").Path(write_norm(tmp_path, VALID)))
    assert ev.schema == EVIDENCE_SCHEMA
    assert ev.run_id == "run-1"
    assert ev.revision == "abc123"
    assert ev.provider == "pytest"
    assert ev.status == "pass"
    assert ev.tests[0].framework_id == "tests.app.test_a"
    assert ev.tests[0].test_uid is None  # no trace_id in the entry


def test_parse_normalized_binds_test_uid_from_trace_id(tmp_path):
    data = dict(VALID)
    data["tests"] = [
        {"framework_id": "tests.app.test_a", "outcome": "pass", "trace_id": "TEST:A"}
    ]
    ev = parse_normalized(__import__("pathlib").Path(write_norm(tmp_path, data)))
    from tracelayer.evidence.models import entity_uid_for

    assert ev.tests[0].test_uid == entity_uid_for("TEST:A")


def test_parse_normalized_preserves_per_test_edge_metadata(tmp_path):
    data = dict(VALID)
    data["execution_edges"] = [
        {
            "test_uid": "n_t",
            "implementation_uid": "n_i",
            "coverage_kind": "per_test",
            "hit_count": 3,
            "metadata": {"behavioral": True},
        }
    ]
    ev = parse_normalized(__import__("pathlib").Path(write_norm(tmp_path, data)))
    assert ev.execution_edges[0].coverage_kind == "per_test"
    assert ev.execution_edges[0].metadata["behavioral"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("schema"),
        lambda d: d.update({"schema": "other/v1"}),
        lambda d: d.pop("run_id"),
        lambda d: d.update({"run_id": ""}),
        lambda d: d.update({"status": "maybe"}),
        lambda d: d.update({"tests": [{"outcome": "pass"}]}),  # no framework_id
        lambda d: d.update({"tests": [{"framework_id": "x", "outcome": "exploded"}]}),
        lambda d: d.update({"tests": "nope"}),
        lambda d: d.update(
            {
                "execution_edges": [
                    {"implementation_uid": "n_i", "coverage_kind": "per_test"}
                ]
            }
        ),  # missing test_uid
        lambda d: d.update(
            {
                "execution_edges": [
                    {"test_uid": "n_t", "implementation_uid": "n_i",
                     "coverage_kind": "bogus"}
                ]
            }
        ),
    ],
)
def test_parse_normalized_rejects_malformed(tmp_path, mutate):
    data = json.loads(json.dumps(VALID))
    mutate(data)
    with pytest.raises(EvidenceFormatError):
        parse_normalized(__import__("pathlib").Path(write_norm(tmp_path, data)))


def test_parse_normalized_rejects_non_object_json(tmp_path):
    p = tmp_path / "evidence.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(EvidenceFormatError):
        parse_normalized(p)


def test_parse_normalized_rejects_malformed_json(tmp_path):
    p = tmp_path / "evidence.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvidenceFormatError):
        parse_normalized(p)


# --------------------------------------------------------------------------
# proof_level transitions 0/1/2/3
# --------------------------------------------------------------------------

def test_proof_level_0_declared_only(project, store):
    assert proof_level(store, "n_test", "n_impl") == 0


def test_proof_level_1_suite_edge(project, store):
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1", test_uid="suite",
                implementation_uid="n_impl", coverage_kind="suite", hit_count=2,
            )
        ],
    )
    # suite edge proves suite execution, not this test's
    assert proof_level(store, "n_test", "n_impl") == 1


def test_proof_level_2_per_test_edge(project, store):
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1", test_uid="n_test",
                implementation_uid="n_impl", coverage_kind="per_test", hit_count=4,
            )
        ],
    )
    assert proof_level(store, "n_test", "n_impl") == 2


def test_proof_level_3_behavioral_metadata(project, store):
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1", test_uid="n_test",
                implementation_uid="n_impl", coverage_kind="per_test", hit_count=4,
                metadata={"behavioral": True},
            )
        ],
    )
    assert proof_level(store, "n_test", "n_impl") == 3


def test_proof_level_per_test_edge_for_other_implementation_not_counted(project, store):
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1", test_uid="n_test",
                implementation_uid="n_other", coverage_kind="per_test", hit_count=1,
            )
        ],
    )
    assert proof_level(store, "n_test", "n_impl") == 0


def test_proof_level_suite_does_not_satisfy_per_test_pair(project, store):
    """Aggregate coverage cannot falsely claim L2 for a specific test."""
    store.add_evidence_run("run-1", None, "pytest", None, None, None, "pass", None, {})
    store.add_execution_edges(
        "run-1",
        [
            ExecutionRecord(
                run_id="run-1", test_uid="suite",
                implementation_uid="n_impl", coverage_kind="suite", hit_count=9,
            )
        ],
    )
    assert proof_level(store, "n_test", "n_impl") == 1  # never 2


# --------------------------------------------------------------------------
# evidence_is_current — revision + fingerprint freshness
# --------------------------------------------------------------------------

def _run_row(store, *, revision: str | None = "abc123", metadata=None) -> dict:
    import json as _json

    store.add_evidence_run(
        "run-1", revision, "pytest", None, None, None, "pass", None, metadata or {},
    )
    row = dict(store.latest_evidence_run())
    row["metadata"] = _json.loads(row.get("metadata_json") or "{}")
    return row


def test_current_with_matching_revision(project, store):
    row = _run_row(store)
    ok, why = evidence_is_current(store, row, "abc123")
    assert (ok, why) == (True, "current")


def test_current_no_evidence(project, store):
    ok, why = evidence_is_current(store, None, "abc123")
    assert (ok, why) == (False, "no-evidence")


def test_revision_mismatch_fails(project, store):
    row = _run_row(store, revision="old-rev")
    ok, why = evidence_is_current(store, row, "new-rev")
    assert (ok, why) == (False, "revision-mismatch")


def test_missing_revision_fails_when_required(project, store):
    row = _run_row(store, revision=None, metadata={"require_revision": True})
    ok, why = evidence_is_current(store, row, "abc123")
    assert (ok, why) == (False, "missing-revision")


def test_missing_revision_allowed_without_require_revision(project, store):
    row = _run_row(store, revision=None, metadata={"require_revision": False})
    ok, why = evidence_is_current(store, row, "abc123")
    assert (ok, why) == (True, "current")


def test_fingerprint_mismatch_fails(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py",
                     fingerprint="fp-v2")
    store.replace_all([impl], [])
    row = _run_row(store)
    store.add_verification_binding("run-1", impl.entity_uid, "fp-v1", "abc123", "pass")
    ok, why = evidence_is_current(store, row, "abc123", target_uid=impl.entity_uid)
    assert (ok, why) == (False, "fingerprint-mismatch")


def test_fingerprint_match_passes(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py",
                     fingerprint="fp-v2")
    store.replace_all([impl], [])
    row = _run_row(store)
    store.add_verification_binding("run-1", impl.entity_uid, "fp-v2", "abc123", "pass")
    ok, why = evidence_is_current(store, row, "abc123", target_uid=impl.entity_uid)
    assert (ok, why) == (True, "current")


def test_no_binding_means_fingerprint_not_checked(project, store):
    impl = make_node("IMPL:1", "implementation", path="src/app.py",
                     fingerprint="fp-v2")
    store.replace_all([impl], [])
    row = _run_row(store)
    ok, why = evidence_is_current(store, row, "abc123", target_uid=impl.entity_uid)
    assert (ok, why) == (True, "current")
