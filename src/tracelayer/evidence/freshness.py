"""Evidence freshness and proof levels (spec 25.2, 25.3; FR-012)."""

from __future__ import annotations

import json
from typing import Any

from tracelayer.graph.store import GraphStore


def proof_level(store: GraphStore, test_uid: str, implementation_uid: str) -> int:
    """Return the strongest execution proof for (test, implementation).

    Level 0: no execution evidence (declared-only ``exercises`` edges).
    Level 1: a suite-level execution edge covers the implementation.
    Level 2: a per-test execution edge for this exact test/implementation.
    Level 3: a per-test edge marked ``metadata["behavioral"] == True``.

    Suite-level edges are bound to the sentinel ``"suite"`` test uid, so
    they cannot satisfy the per-test checks (spec 17.7: aggregate coverage
    proves suite execution, not a specific test).
    """
    per_test = [
        e
        for e in store.execution_edges_for_test(test_uid)
        if e.implementation_uid == implementation_uid
    ]
    if per_test:
        if any(bool(e.metadata.get("behavioral")) for e in per_test):
            return 3
        return 2
    if any(e.coverage_kind == "suite" for e in store.execution_edges_for(implementation_uid)):
        return 1
    return 0


def _metadata_of(value: Any) -> dict:
    """Deserialize evidence-run metadata, which may be a dict or JSON text."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def evidence_is_current(
    store: GraphStore,
    run_row: dict | None,
    revision: str | None,
    target_uid: str | None = None,
) -> tuple[bool, str]:
    """Whether an evidence run is current for the evaluated revision.

    Current requires: (a) a revision match — a run without a revision is
    only acceptable when it was ingested without ``require_revision`` (the
    flag is carried in the run's metadata by ingest), and (b) when a
    verification binding exists for ``target_uid``, at least one binding
    whose ``target_fingerprint`` equals the node's current artifact
    fingerprint (spec 25.3).
    """
    if not run_row:
        return False, "no-evidence"
    require_revision = bool(_metadata_of(run_row.get("metadata")).get("require_revision", True))
    run_rev = run_row.get("revision")
    if require_revision and not run_rev:
        return False, "missing-revision"
    if run_rev and revision is not None and run_rev != revision:
        return False, "revision-mismatch"
    if target_uid is not None:
        node = store.get_node(uid=target_uid)
        current_fp = node.artifact_fingerprint if node is not None else None
        bindings = store.bindings_for(target_uid)
        if bindings and current_fp:
            if not any(b.get("target_fingerprint") == current_fp for b in bindings):
                return False, "fingerprint-mismatch"
    return True, "current"
