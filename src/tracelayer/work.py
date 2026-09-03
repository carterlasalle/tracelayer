"""Native work/task/question lifecycle states and readiness (spec Sections 6-9, 35).

Beads-free by design: readiness derives from task states, blocking edges,
and open questions in the TraceLayer graph alone (spec Section 74).
Optional Beads integration may enhance this later; it must never gate it.
"""

from __future__ import annotations

from tracelayer.graph.store import GraphStore

TASK_STATES = (
    "TODO",
    "READY",
    "IN_PROGRESS",
    "PARTIALLY_COMPLETE",
    "BLOCKED",
    "WAITING_FOR_DECISION",
    "WAITING_FOR_INPUT",
    "DEFERRED",
    "DONE",
    "CANCELLED",
    "NOT_IMPLEMENTED",
)

# Display aliases normalized to canonical storage (spec Section 7).
TASK_STATE_ALIASES = {
    "HALF_FINISHED": "PARTIALLY_COMPLETE",
    "HALF-FINISHED": "PARTIALLY_COMPLETE",
    "PARTIAL": "PARTIALLY_COMPLETE",
    "UNFINISHED": "TODO",
    "UNIMPLEMENTED": "NOT_IMPLEMENTED",
    "FOLLOW_UP": "TODO",
    "FOLLOW-UP": "TODO",
    "FOLLOWUP": "TODO",
    "IN-PROGRESS": "IN_PROGRESS",
    "INPROGRESS": "IN_PROGRESS",
    "WAITING-FOR-DECISION": "WAITING_FOR_DECISION",
    "WAITING-FOR-INPUT": "WAITING_FOR_INPUT",
    "PARTIALLY-COMPLETE": "PARTIALLY_COMPLETE",
    "NOT-IMPLEMENTED": "NOT_IMPLEMENTED",
}

QUESTION_STATES = (
    "OPEN",
    "ANSWERED",
    "SUPERSEDED",
    "NO_LONGER_RELEVANT",
    "DEFERRED",
)

WORK_STATES = (
    "ACTIVE",
    "DONE",
    "PARTIALLY_COMPLETE",
    "DEFERRED",
    "CANCELLED",
)

TERMINAL_TASK_STATES = frozenset({"DONE", "CANCELLED"})
STATE_BLOCKED_TASK_STATES = frozenset({"BLOCKED", "WAITING_FOR_DECISION", "WAITING_FOR_INPUT"})


# trace:exempt reason=internal-helper
def _canon(value: object) -> str:
    """Uppercase, whitespace/hyphen-insensitive canonical form."""
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


# trace:exempt reason=internal-helper
def normalize_task_state(value: object, default: str = "TODO") -> str:
    """Canonical task state for a marker ``state=`` value (spec Section 7)."""
    canon = TASK_STATE_ALIASES.get(_canon(value), _canon(value))
    return canon if canon in TASK_STATES else default


# trace:exempt reason=internal-helper
def normalize_question_state(value: object, default: str = "OPEN") -> str:
    """Canonical question state for a marker ``state=`` value (spec Section 14)."""
    canon = _canon(value)
    if canon in ("NOT_RELEVANT", "IRRELEVANT", "OBSOLETE"):
        return "NO_LONGER_RELEVANT"
    return canon if canon in QUESTION_STATES else default


# trace:exempt reason=internal-helper
def normalize_work_state(value: object, default: str = "ACTIVE") -> str:
    """Canonical work state (spec Section 72)."""
    canon = TASK_STATE_ALIASES.get(_canon(value), _canon(value))
    return canon if canon in WORK_STATES else default


# trace:v1 id=impl.work.readiness work=WORK-trace-layer-native-work-task-question-decision-model satisfies=REQ-native-ready-state-computation
def compute_readiness(store: GraphStore, work_id: str) -> dict:
    """READY/BLOCKED computation for one work item without Beads (spec Section 35).

    A non-terminal task is READY when no unresolved task/question blocks it:
    ``blocked_by`` / ``depends_on`` / ``asks`` targets and incoming ``blocks``
    edges whose task state is not DONE/CANCELLED, or whose question is OPEN.
    Edges to other node types carry no lifecycle and are ignored.
    """
    work = store.get_node(trace_id=work_id)
    if work is None or not work.active:
        raise ValueError(f"no active work node: {work_id}")
    by_uid = {n.entity_uid: n for n in store.all_nodes(active_only=True)}
    member_uids = {
        e.from_uid for e in store.edges_to(work.entity_uid, "work") if e.status == "active"
    }
    tasks = sorted(
        (by_uid[u] for u in member_uids if u in by_uid and by_uid[u].node_type == "task"),
        key=lambda n: n.trace_id,
    )
    titles = {n.trace_id: n.title or n.trace_id for n in by_uid.values()}

    # trace:exempt reason=internal-helper
    def blockers(task_uid: str) -> list[str]:
        reasons: list[str] = []
        seen: set[str] = set()
        targets: list[tuple[str, str]] = []
        for e in store.edges_from(task_uid, "blocked_by"):
            targets.append((e.to_uid, "blocked by"))
        for e in store.edges_from(task_uid, "depends_on"):
            targets.append((e.to_uid, "depends on"))
        for e in store.edges_from(task_uid, "asks"):
            targets.append((e.to_uid, "asks"))
        for e in store.edges_to(task_uid, "blocks"):
            if e.status == "active":
                targets.append((e.from_uid, "blocked by"))
        for to_uid, via in targets:
            node = by_uid.get(to_uid)
            if node is None or not node.active or node.trace_id in seen:
                continue
            if node.node_type == "task":
                state = normalize_task_state(node.metadata.get("state"))
                if state not in TERMINAL_TASK_STATES:
                    seen.add(node.trace_id)
                    reasons.append(f"{via} {node.trace_id} ({state})")
            elif node.node_type == "question":
                state = normalize_question_state(node.metadata.get("state"))
                if state == "OPEN":
                    seen.add(node.trace_id)
                    reasons.append(f"waiting on open question {node.trace_id} ({via})")
        return reasons

    result: dict = {
        "work": work_id,
        "ready": [],
        "in_progress": [],
        "partial": [],
        "blocked": {},
        "open_questions": [],
        "done": [],
        "deferred": [],
        "cancelled": [],
        "not_implemented": [],
        "titles": titles,
    }
    for task in tasks:
        tid = task.trace_id
        state = normalize_task_state(task.metadata.get("state"))
        if state == "DONE":
            result["done"].append(tid)
        elif state == "CANCELLED":
            result["cancelled"].append(tid)
        elif state == "DEFERRED":
            result["deferred"].append(tid)
        elif state == "NOT_IMPLEMENTED":
            result["not_implemented"].append(tid)
        elif state == "IN_PROGRESS":
            result["in_progress"].append(tid)
        elif state == "PARTIALLY_COMPLETE":
            result["partial"].append(tid)
        else:
            reasons = blockers(task.entity_uid)
            if state in STATE_BLOCKED_TASK_STATES:
                reasons = [f"state={state}"] + reasons
            if reasons:
                result["blocked"][tid] = reasons
            else:
                result["ready"].append(tid)
    for uid in sorted(member_uids):
        node = by_uid.get(uid)
        if (
            node is not None
            and node.node_type == "question"
            and normalize_question_state(node.metadata.get("state")) == "OPEN"
        ):
            result["open_questions"].append(node.trace_id)
    result["open_questions"].sort()
    return result

IMPL_STATES = ("PLANNED", "PARTIAL", "IMPLEMENTED", "DEPRECATED", "REMOVED")

FULFILLMENT_STATES = (
    "UNIMPLEMENTED",
    "PARTIALLY_IMPLEMENTED",
    "IMPLEMENTED",
    "VERIFIED",
    "STALE",
    "DEPRECATED",
)


# trace:exempt reason=internal-helper
def normalize_impl_state(value: object, default: str = "IMPLEMENTED") -> str:
    """Canonical implementation state (spec Section 40)."""
    canon = _canon(value)
    return canon if canon in IMPL_STATES else default


# trace:v1 id=impl.work.fulfillment work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status satisfies=REQ-fulfillment-status
def fulfillment(store: GraphStore, requirement_id: str) -> dict:
    """Derived requirement fulfillment (spec Section 41).

    UNIMPLEMENTED when no implementation satisfies the requirement,
    PARTIALLY_IMPLEMENTED when any linked implementation is PLANNED/PARTIAL,
    VERIFIED when linked tests exist and all currently pass, else IMPLEMENTED.
    Retired requirements report DEPRECATED; stale ones report STALE.
    """
    node = store.get_node(trace_id=requirement_id)
    if node is None or not node.active:
        raise ValueError(f"no active requirement node: {requirement_id}")
    if node.status() == "retired":
        return {"requirement": requirement_id, "status": "DEPRECATED",
                "implementations": [], "tests": []}
    impls = sorted(
        {
            src.trace_id
            for e in store.edges_to(node.entity_uid, "satisfies")
            if e.status == "active"
            for src in [store.get_node(uid=e.from_uid)]
            if src is not None and src.active and src.node_type == "implementation"
        }
    )
    if not impls:
        return {"requirement": requirement_id, "status": "UNIMPLEMENTED",
                "implementations": [], "tests": []}
    tests = sorted(
        {
            src.trace_id
            for e in store.edges_to(node.entity_uid, "verifies")
            if e.status == "active"
            for src in [store.get_node(uid=e.from_uid)]
            if src is not None and src.active and src.node_type == "test"
        }
    )
    impl_nodes = [n for n in (store.get_node(trace_id=i) for i in impls) if n is not None]
    partial = any(
        normalize_impl_state(n.metadata.get("state")) in ("PLANNED", "PARTIAL")
        for n in impl_nodes
    )
    if node.status() != "current":
        status = "STALE"
    elif partial:
        status = "PARTIALLY_IMPLEMENTED"
    elif tests and all(
        _test_passes(store, store.get_node(trace_id=t)) for t in tests
    ):
        status = "VERIFIED"
    else:
        status = "IMPLEMENTED"
    return {"requirement": requirement_id, "status": status,
            "implementations": impls, "tests": tests}


# trace:exempt reason=internal-helper
def _test_passes(store: GraphStore, test) -> bool:
    """Latest outcome for a test node is a current pass."""
    from tracelayer.graph.models import Node

    if not isinstance(test, Node) or test.status() != "current":
        return False
    framework_id = test.metadata.get("framework_test_id") or test.trace_id
    latest = store.latest_outcome(framework_id)
    return latest is not None and latest.outcome == "pass"
