"""Beads detection: optional enhancement, never a dependency (spec Sections 4-5, 74).

TraceLayer natively provides work, tasks, dependencies, questions, decisions,
and ready/blocked state. When Beads is installed AND initialized for the
repository, integrations may enhance queues and coordination on top.
Detection only reads; TraceLayer never initializes Beads on its own.
"""

from __future__ import annotations

import re
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

MIRROR_FILE = ".trace/beads-mirror.toml"
MIRROR_REF_PREFIX = "TRACE:"


# trace:exempt reason=internal-helper
def run_bd(root: Path | str, *args: str, timeout: int = 60):
    """Run the bd CLI in ``root``; returns (returncode, stdout, stderr)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["bd", *args], capture_output=True, text=True, cwd=str(root), timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


# trace:exempt reason=internal-helper
def bd_beads(root: Path | str) -> list[dict]:
    """All beads (open and closed) as dicts; [] when Beads is unusable."""
    import json

    rc, out, _ = run_bd(root, "list", "--json", "--all")
    if rc != 0:
        return []
    try:
        items = json.loads(out or "[]")
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("id")]


# trace:exempt reason=internal-helper
def read_mirror(root: Path | str) -> dict:
    """TASK id -> bead id mapping (persisted mirror state)."""
    import tomllib

    path = Path(root) / MIRROR_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    mirror = data.get("mirror", {})
    return dict(mirror) if isinstance(mirror, dict) else {}


# trace:exempt reason=internal-helper
def write_mirror(root: Path | str, mapping: dict) -> None:
    """Persist TASK id -> bead id mapping (tracked operational state)."""
    path = Path(root) / MIRROR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[mirror]"]
    for task_id in sorted(mapping):
        bead = str(mapping[task_id]).replace('"', "")
        lines.append(f'"{task_id}" = "{bead}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# trace:v1 id=impl.beads.mirror work=WORK-beads-task-mirror-with-completion-reconciliation satisfies=REQ-task-mirror-with-mapping
def mirror_tasks(store, root: Path | str, work_id: str, *, apply: bool = False) -> dict:
    """Mirror native TASKs into Beads (spec Section 33).

    Preview by default; ``apply=True`` creates missing beads (tagged
    ``TRACE:<TASK-ID>``), links mirrored blockers, closes beads whose task
    is DONE/CANCELLED, and persists the mapping. Never initializes Beads.
    """
    from tracelayer.work import TERMINAL_TASK_STATES, compute_readiness, normalize_task_state

    root = Path(root)
    work = store.get_node(trace_id=work_id)
    if work is None or not work.active:
        raise ValueError(f"no active work node: {work_id}")
    if not detect_beads(root)["beads"]["active"]:
        raise ValueError("beads not active for this repository")
    readiness = compute_readiness(store, work_id)
    order = (
        readiness["ready"] + readiness["in_progress"] + readiness["partial"]
        + list(readiness["blocked"]) + readiness["done"] + readiness["cancelled"]
        + readiness["deferred"] + readiness["not_implemented"]
    )
    states = {}
    for tid in order:
        node = store.get_node(trace_id=tid)
        states[tid] = normalize_task_state(node.metadata.get("state") if node else None)
    mapping = read_mirror(root)
    beads = {b.get("external_ref"): b for b in bd_beads(root)}
    created: list[dict] = []
    linked: list[dict] = []
    skipped: list[dict] = []
    for tid in order:
        bead_id = mapping.get(tid)
        if bead_id is None:
            bead = beads.get(f"{MIRROR_REF_PREFIX}{tid}")
            bead_id = bead.get("id") if bead else None
            if bead_id is not None:
                mapping[tid] = bead_id
        if bead_id is not None:
            skipped.append({"task": tid, "bead": bead_id})
            continue
        if not apply:
            created.append({"task": tid, "bead": None, "preview": True})
            continue
        task_node = store.get_node(trace_id=tid)
        title = (task_node.title or tid) if task_node is not None else tid
        rc, out, err = run_bd(root, "create", title, "--external-ref", f"{MIRROR_REF_PREFIX}{tid}")
        if rc != 0:
            created.append({"task": tid, "bead": None, "error": (err or out).strip()[:200]})
            continue
        match = re.search(r"Created issue:\s+(\S+)", out)
        bead_id = match.group(1) if match else None
        if not bead_id:
            created.append({"task": tid, "bead": None, "error": "no bead id returned"})
            continue
        mapping[tid] = bead_id
        created.append({"task": tid, "bead": bead_id})
        if states[tid] in TERMINAL_TASK_STATES:
            run_bd(root, "close", bead_id)
    if apply:
        by_bead = {t: b for t, b in mapping.items()}
        for tid in order:
            node = store.get_node(trace_id=tid)
            if node is None:
                continue
            for edge in store.edges_from(node.entity_uid):
                if edge.predicate == "blocked_by":
                    dep_type, via = "blocks", True
                elif edge.predicate == "discovered_from":
                    dep_type, via = "discovered-from", True
                else:
                    continue
                target = store.get_node(uid=edge.to_uid)
                if target is None or target.node_type != "task":
                    continue
                blocker_bead = by_bead.get(target.trace_id)
                if blocker_bead is None or blocker_bead == by_bead.get(tid):
                    continue
                args = ["link", by_bead[tid], blocker_bead]
                if dep_type != "blocks":
                    args += ["--type", dep_type]
                rc, _, _ = run_bd(root, *args)
                if rc == 0 and via:
                    linked.append({"task": tid, "bead": by_bead[tid],
                                   "blocks": blocker_bead, "type": dep_type})
        write_mirror(root, mapping)
    return {"work": work_id, "created": created, "linked": linked, "skipped": skipped,
            "mapping": dict(mapping)}


# trace:v1 id=impl.beads.reconcile work=WORK-beads-task-mirror-with-completion-reconciliation satisfies=REQ-completion-reconciliation
def reconcile(store, root: Path | str, work_id: str) -> dict:
    """Beads-vs-TraceLayer completion check (spec Sections 37, 39)."""
    from tracelayer.work import TERMINAL_TASK_STATES, compute_readiness, normalize_task_state

    root = Path(root)
    if not detect_beads(root)["beads"]["active"]:
        raise ValueError("beads not active for this repository")
    readiness = compute_readiness(store, work_id)
    mapping = read_mirror(root)
    beads = {b.get("id"): b for b in bd_beads(root)}
    mismatches: list[dict] = []
    question_blocked: list[dict] = []
    for tid, bead_id in sorted(mapping.items()):
        bead = beads.get(bead_id)
        if bead is None:
            mismatches.append({"task": tid, "bead": bead_id, "issue": "bead missing in Beads"})
            continue
        node = store.get_node(trace_id=tid)
        state = normalize_task_state(node.metadata.get("state") if node else None)
        if str(bead.get("status", "")).lower() == "closed" and state not in TERMINAL_TASK_STATES:
            mismatches.append({"task": tid, "bead": bead_id,
                               "issue": "closed in Beads but TraceLayer state is " + state})
    for tid, reasons in readiness["blocked"].items():
        if any("open question" in r for r in reasons):
            bead_id = mapping.get(tid)
            question_blocked.append({"task": tid, "bead": bead_id, "reasons": reasons,
                                     "note": "answer in TraceLayer; reflect blockage in Beads"})
    open_questions = readiness.get("open_questions", [])
    return {"work": work_id, "mismatches": mismatches, "question_blocked": question_blocked,
            "open_questions": open_questions,
            "complete": not mismatches and not question_blocked}
