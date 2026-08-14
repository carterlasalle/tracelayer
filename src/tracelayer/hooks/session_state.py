"""File-backed per-session hook state (spec 22.3).

State lives in the git-ignored cache: ``project.session_dir/{session_id}.json``.
Writes are atomic (temp file + ``os.replace``). Missing or corrupt files
degrade to empty state and are rewritten on the next mutation — hook state
must never break the agent loop.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from tracelayer.config import Project

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


# trace:v1 id=impl.hooks.session-state work=WORK-TL-001
class SessionState:
    """JSON-file-backed session state keyed by session id."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.session_dir = project.session_dir

    # -- identity ---------------------------------------------------------

    def session_id_from(self, payload: dict) -> str:
        """Payload ``session_id``, else the ``TRACE_SESSION`` env, else ``"default"``."""
        sid = payload.get("session_id") or os.environ.get("TRACE_SESSION")
        return str(sid or "default")

    def _file(self, session_id: str) -> Path:
        # Sanitize so hostile session ids cannot escape the session directory.
        name = _SAFE.sub("-", str(session_id)).strip("-.")
        if not name or name in {".", ".."}:
            name = "default"
        return self.session_dir / f"{name}.json"

    # -- storage ----------------------------------------------------------

    def _read(self, session_id: str) -> dict:
        path = self._file(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, session_id: str, data: dict) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self._file(session_id)
        fd, tmp = tempfile.mkstemp(dir=self.session_dir, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- accessors ----------------------------------------------------------

    def context_loaded(self, session_id: str, trace_id: str) -> bool:
        return trace_id in self._read(session_id).get("contexts_loaded", [])

    def record_context_load(self, session_id: str, trace_id: str) -> None:
        data = self._read(session_id)
        loaded = data.setdefault("contexts_loaded", [])
        if trace_id not in loaded:
            loaded.append(trace_id)
        self._write(session_id, data)

    def record_blocked_edit(self, session_id: str, trace_id: str) -> None:
        data = self._read(session_id)
        blocked = data.setdefault("blocked", [])
        if trace_id not in blocked:
            blocked.append(trace_id)
        self._write(session_id, data)

    def blocked_without_context(self, session_id: str, trace_id: str) -> bool:
        return trace_id in self._read(session_id).get("blocked", [])

    def mark_dirty(self, session_id: str, trace_ids: set[str]) -> None:
        data = self._read(session_id)
        dirty = data.setdefault("dirty", [])
        for tid in sorted(trace_ids):
            if tid not in dirty:
                dirty.append(tid)
        self._write(session_id, data)

    def dirty(self, session_id: str) -> set[str]:
        return set(self._read(session_id).get("dirty", []))

    def set_active_work(self, session_id: str, work_id: str | None) -> None:
        """Record the work item the session is operating under (spec 22.4)."""
        if work_id is None:
            return
        data = self._read(session_id)
        data["active_work"] = work_id
        self._write(session_id, data)

    def active_work(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_work")

    def set_active_plan(self, session_id: str, plan_id: str | None) -> None:
        """Record the plan the session is implementing (review P2)."""
        if plan_id is None:
            return
        data = self._read(session_id)
        data["active_plan"] = plan_id
        self._write(session_id, data)

    def active_plan(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_plan")

    def set_active_requirement(self, session_id: str, req_id: str | None) -> None:
        """Record the requirement the session is operating under (spec 22.4)."""
        if req_id is None:
            return
        data = self._read(session_id)
        data["active_requirement"] = req_id
        self._write(session_id, data)

    def active_requirement(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_requirement")

    # -- pending trace obligations (durable authoring, not ephemeral prose) --

    def add_obligation(self, session_id: str, obligation: dict) -> None:
        """Persist a pending trace-authoring obligation for the session.

        Obligations are durable: the agent must resolve them (by adding the
        marker) before the stop gate allows completion.
        """
        data = self._read(session_id)
        obligations = data.setdefault("obligations", [])
        key = (obligation.get("path"), obligation.get("symbol"))
        for existing in obligations:
            if (existing.get("path"), existing.get("symbol")) == key:
                return  # dedupe by path+symbol
        obligations.append(obligation)
        self._write(session_id, data)

    def resolve_obligation(self, session_id: str, path: str, symbol: str) -> None:
        data = self._read(session_id)
        changed = False
        for existing in data.get("obligations", []):
            if (existing.get("path"), existing.get("symbol")) == (path, symbol):
                existing["state"] = "satisfied"
                changed = True
        if changed:
            self._write(session_id, data)

    def pending_obligations(self, session_id: str) -> list[dict]:
        return [
            o
            for o in self._read(session_id).get("obligations", [])
            if o.get("state") != "satisfied"
        ]

    def clear(self, session_id: str) -> None:
        """Reset the session to a clean slate (empty file, not deleted)."""
        self._write(
            session_id,
            {
                "contexts_loaded": [],
                "blocked": [],
                "dirty": [],
                "active_work": None,
                "active_requirement": None,
                "active_plan": None,
                "obligations": [],
            },
        )
