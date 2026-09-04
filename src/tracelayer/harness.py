"""Harness TODO adapters: native TODOs become durable TraceLayer tasks (spec Sections 10-11).

Supported harnesses map their todo events onto canonical task dicts; the
plan-doc sync persists them as TASK nodes under the active work/plan so the
list survives compaction, session end, and model change. Harness metadata is
preserved as origin, never as the canonical id.
"""

from __future__ import annotations

from tracelayer.graph.store import GraphStore
from tracelayer.protocol.ids import generate_id
from tracelayer.work import normalize_task_state

HARNESSES = ("claude", "omp", "codex")

# Harness status token -> canonical task state (spec Section 7).
_STATUS_MAP = {
    "pending": "TODO",
    "todo": "TODO",
    "unstarted": "TODO",
    "ready": "READY",
    "in_progress": "IN_PROGRESS",
    "doing": "IN_PROGRESS",
    "started": "IN_PROGRESS",
    "partial": "PARTIALLY_COMPLETE",
    "blocked": "BLOCKED",
    "waiting": "WAITING_FOR_INPUT",
    "deferred": "DEFERRED",
    "completed": "DONE",
    "complete": "DONE",
    "done": "DONE",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
}


# trace:exempt reason=internal-helper
def _todo_title(item: dict) -> str:
    """Human title from a harness todo (content/title/subject, first line)."""
    for key in ("content", "title", "subject", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return ""


# trace:v1 id=impl.harness.normalize work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status satisfies=REQ-harness-todo-adapters
def normalize_todos(harness: str, items: list[dict]) -> list[dict]:
    """Canonical task dicts from one harness's todo list.

    Each dict carries title, state, and origin (harness + native ref).
    Unknown statuses fall back to TODO; untitled entries are dropped.
    """
    name = str(harness or "").strip().lower()
    if name not in HARNESSES:
        raise ValueError(f"unknown harness {harness!r}; choose from {', '.join(HARNESSES)}")
    tasks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _todo_title(item)
        if not title:
            continue
        raw = str(item.get("status") or "pending").strip().lower().replace(" ", "_")
        state = _STATUS_MAP.get(raw, "TODO")
        origin: dict = {"harness": name}
        for key in ("id", "key", "ref"):
            if item.get(key) is not None:
                origin["ref"] = str(item[key])[:80]
                break
        tasks.append({"title": title, "state": normalize_task_state(state), "origin": origin})
    return tasks


# trace:v1 id=impl.harness.sync-blocks work=WORK-harness-todo-sync-beads-detection-and-fulfillment-status satisfies=REQ-harness-todo-adapters
def render_task_blocks(store: GraphStore, work_id: str, tasks: list[dict]) -> str:
    """Markdown plan-doc blocks with TASK markers for normalized todos."""
    taken = {n.trace_id for n in store.all_nodes(active_only=False)}
    chunks = []
    for task in tasks:
        tid = generate_id("task", task["title"], taken=taken)
        taken.add(tid)
        origin = " ".join(f"{k}={v}" for k, v in sorted(task.get("origin", {}).items()))
        chunks.append(
            f"## {tid} — {task['title']}\n"
            f"\n"
            f"<!-- trace:v1 id={tid} type=task state={task['state']} work={work_id} -->\n"
            f"\n" + (f"Origin: {origin}\n" if origin else "")
        )
    return "\n".join(chunks)
