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

    # trace:exempt reason=internal-helper
    def __init__(self, project: Project) -> None:
        self.project = project
        self.session_dir = project.session_dir

    # -- identity ---------------------------------------------------------

    # trace:exempt reason=internal-helper
    def session_id_from(self, payload: dict) -> str:
        """Payload ``session_id``, else the ``TRACE_SESSION`` env, else ``"default"``."""
        sid = payload.get("session_id") or os.environ.get("TRACE_SESSION")
        return str(sid or "default")

    # trace:exempt reason=internal-helper
    def _file(self, session_id: str) -> Path:
        # Sanitize so hostile session ids cannot escape the session directory.
        name = _SAFE.sub("-", str(session_id)).strip("-.")
        if not name or name in {".", ".."}:
            name = "default"
        return self.session_dir / f"{name}.json"

    # -- storage ----------------------------------------------------------

    # trace:exempt reason=internal-helper
    def _read(self, session_id: str) -> dict:
        path = self._file(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # trace:exempt reason=internal-helper
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

    # trace:exempt reason=internal-helper
    def context_loaded(self, session_id: str, trace_id: str) -> bool:
        return trace_id in self._read(session_id).get("contexts_loaded", [])

    # trace:exempt reason=internal-helper
    def record_context_load(self, session_id: str, trace_id: str) -> None:
        data = self._read(session_id)
        loaded = data.setdefault("contexts_loaded", [])
        if trace_id not in loaded:
            loaded.append(trace_id)
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def record_blocked_edit(self, session_id: str, trace_id: str) -> None:
        data = self._read(session_id)
        blocked = data.setdefault("blocked", [])
        if trace_id not in blocked:
            blocked.append(trace_id)
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def blocked_without_context(self, session_id: str, trace_id: str) -> bool:
        return trace_id in self._read(session_id).get("blocked", [])

    # trace:exempt reason=internal-helper
    def mark_dirty(self, session_id: str, trace_ids: set[str]) -> None:
        data = self._read(session_id)
        dirty = data.setdefault("dirty", [])
        for tid in sorted(trace_ids):
            if tid not in dirty:
                dirty.append(tid)
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def dirty(self, session_id: str) -> set[str]:
        return set(self._read(session_id).get("dirty", []))

    # trace:exempt reason=internal-helper
    def set_active_work(self, session_id: str, work_id: str | None) -> None:
        """Record the work item the session is operating under (spec 22.4)."""
        if work_id is None:
            return
        data = self._read(session_id)
        data["active_work"] = work_id
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def active_work(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_work")

    # trace:exempt reason=internal-helper
    def set_active_plan(self, session_id: str, plan_id: str | None) -> None:
        """Record the plan the session is implementing (review P2)."""
        if plan_id is None:
            return
        data = self._read(session_id)
        data["active_plan"] = plan_id
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def active_plan(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_plan")

    # trace:exempt reason=internal-helper
    def set_active_requirement(self, session_id: str, req_id: str | None) -> None:
        """Record the (single) primary requirement the session operates under."""
        if req_id is None:
            return
        data = self._read(session_id)
        data["active_requirement"] = req_id
        requirements = data.setdefault("active_requirements", [])
        if req_id not in requirements:
            requirements.append(req_id)
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def active_requirement(self, session_id: str) -> str | None:
        return self._read(session_id).get("active_requirement")

    # trace:exempt reason=internal-helper
    def set_active_requirements(self, session_id: str, req_ids: list[str]) -> None:
        """Record the full set of requirements the task implements (Ambient §16)."""
        if not req_ids:
            return
        data = self._read(session_id)
        ordered: list[str] = []
        for rid in req_ids:
            if rid not in ordered:
                ordered.append(rid)
        data["active_requirements"] = ordered
        data["active_requirement"] = ordered[0]  # primary for back-compat
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def active_requirements(self, session_id: str) -> list[str]:
        """All requirements active for the task (plural; Ambient §16)."""
        data = self._read(session_id)
        return list(data.get("active_requirements") or [])

    # trace:exempt reason=internal-helper
    def add_active_requirement(self, session_id: str, req_id: str) -> None:
        data = self._read(session_id)
        requirements = data.setdefault("active_requirements", [])
        if req_id not in requirements:
            requirements.append(req_id)
        if not data.get("active_requirement"):
            data["active_requirement"] = req_id
        self._write(session_id, data)

    # -- pending trace obligations (durable authoring, not ephemeral prose) --

    # trace:exempt reason=internal-helper
    def set_pending_bootstrap(self, session_id: str, prompt_hash: str) -> None:
        """Record that the session's latest intent has no causal context yet.

        The pre-mutation gate turns this into a mandatory semantic-bootstrap
        instruction before the first code mutation (adversarial review P0:
        natural-language intake). Cleared by bootstrap/activate/intake.
        """
        data = self._read(session_id)
        data["pending_bootstrap"] = prompt_hash
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def pending_bootstrap(self, session_id: str) -> str | None:
        return self._read(session_id).get("pending_bootstrap")

    # trace:exempt reason=internal-helper
    def clear_pending_bootstrap(self, session_id: str) -> None:
        data = self._read(session_id)
        if data.pop("pending_bootstrap", None) is not None:
            self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def set_pending_spec_update(self, session_id: str, req_ids: list[str]) -> None:
        """Record requirements whose contract the user just changed.

        The authoring gate blocks implementation edits until the governing
        requirement text actually changes (fingerprint) or the agent
        reclassifies via ``trace task intake`` (adversarial review P0:
        spec evolution must be enforced, not voluntary).
        """
        if not req_ids:
            return
        data = self._read(session_id)
        pending = data.setdefault("pending_spec_update", [])
        for rid in req_ids:
            if rid not in pending:
                pending.append(rid)
        self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def pending_spec_update(self, session_id: str) -> list[str]:
        return list(self._read(session_id).get("pending_spec_update") or [])

    # trace:exempt reason=internal-helper
    def clear_pending_spec_update(self, session_id: str) -> None:
        data = self._read(session_id)
        if data.pop("pending_spec_update", None) is not None:
            self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def add_obligation(self, session_id: str, obligation: dict) -> bool:
        """Persist a pending trace-authoring obligation for the session.

        Obligations are durable: the agent must resolve them (by adding the
        marker) before the stop gate allows completion. Returns True when
        the obligation was newly added, False when it was already pending
        (dedupe by path+symbol) — callers use this to report NEW work vs
        re-listing existing pendings honestly.
        """
        data = self._read(session_id)
        obligations = data.setdefault("obligations", [])
        key = (obligation.get("path"), obligation.get("symbol"))
        for existing in obligations:
            if (existing.get("path"), existing.get("symbol")) == key:
                if existing.get("state") == "satisfied":
                    # The scan re-proposed an obligation for a boundary the
                    # tree already accounts for (marker landed since). Keep
                    # it satisfied — never resurrect cleared work.
                    return False
                # Pending re-proposal: refresh the coaching metadata (work /
                # requirement / suggested marker may have changed since the
                # obligation was first created — e.g. the agent picked a
                # requirement mid-session). State stays pending.
                for field in ("work", "requirement", "suggested_marker", "kind"):
                    if obligation.get(field):
                        existing[field] = obligation[field]
                self._write(session_id, data)
                return False  # dedupe by path+symbol
        obligations.append(obligation)
        self._write(session_id, data)
        return True

    # trace:exempt reason=internal-helper
    def resolve_obligation(self, session_id: str, path: str, symbol: str) -> None:
        data = self._read(session_id)
        changed = False
        for existing in data.get("obligations", []):
            if (existing.get("path"), existing.get("symbol")) == (path, symbol):
                existing["state"] = "satisfied"
                changed = True
        if changed:
            self._write(session_id, data)

    # trace:exempt reason=internal-helper
    def pending_obligations(self, session_id: str) -> list[dict]:
        return [
            o
            for o in self._read(session_id).get("obligations", [])
            if o.get("state") != "satisfied"
        ]

    # trace:exempt reason=internal-helper
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
                "active_requirements": [],
                "active_plan": None,
                "obligations": [],
                "pending_bootstrap": None,
                "pending_spec_update": [],
            },
        )
