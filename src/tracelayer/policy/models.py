"""Policy evaluation domain models (spec Section 24, contract §P).

``EvalContext`` carries everything a single deterministic rule needs to
inspect the graph and the change under evaluation.  ``PolicyResult`` is the
outcome of ``tracelayer.policy.evaluator.evaluate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tracelayer.diagnostics import Diagnostic

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from tracelayer.config import Project
    from tracelayer.graph.store import GraphStore


# trace:exempt  # data container, no behavior
@dataclass
class EvalContext:
    """Per-evaluation state handed to every rule function.

    ``changed_ids`` is the set of trace IDs touched by the current change;
    ``None`` means whole-repo scope.  ``changed_paths`` are repository-relative
    paths of changed files (empty in whole-repo scope).  ``revision`` is the
    evaluated Git revision, when known; ``audit_result`` the parsed
    tracelayer-audit-result/v1 dict, when the caller supplied one.
    """

    project: Project
    store: GraphStore
    lifecycle: str
    changed_ids: set[str] | None  # None => whole-repo scope
    changed_paths: set[str]
    revision: str | None = None
    audit_result: dict | None = None
    gitrepo: object | None = None  # for baseline reads (TL013)


# trace:v1 id=impl.policy.models work=WORK-TL-001
@dataclass
class PolicyResult:
    """Outcome of an evaluation: pass/fail, whether it blocks, diagnostics."""

    status: str  # "pass" | "fail"
    blocking: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
