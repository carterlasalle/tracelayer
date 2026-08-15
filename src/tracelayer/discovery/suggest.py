"""Marker suggestion engine (review: one renderer for every authoring surface).

The pre-hook, ``trace marker suggest``, and the post-hook guidance must
produce the SAME marker for the SAME boundary — no independently assembled
marker strings. The engine classifies the boundary's artifact role, picks
the host language's comment syntax, and renders the exact semantic
relationships from the session context:

    boundary -> role (impl/test/doc/ops/requirement/...) -> host syntax
        -> relationships (work/satisfies/verifies/exercises/implements/
           documents) -> canonical marker line

JSON files cannot carry comments: the suggestion points at a sidecar
(``.trace/sidecars/<path>.json``) instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tracelayer.discovery.boundaries import Boundary

_COMMENT_STYLES = {
    "python": "#",
    "yaml": "#",
    "toml": "#",
    "go": "//",
    "rust": "//",
    "java": "//",
    "typescript": "//",
    "javascript": "//",
    "markdown": "<!-- -->",
    "json": None,  # no comments in JSON: sidecar required
}


# trace:exempt reason=data-container
@dataclass
class MarkerSuggestion:
    """One canonical marker suggestion."""

    marker: str
    role: str
    syntax: str
    sidecar: str | None = None
    note: str = ""
    relationships: list[str] = field(default_factory=list)


def _slug(name: str) -> str:
    """kebab-case slug safe for trace ids.

    Splits camelCase (TS/JS names), spaces, and punctuation: "handleRequest"
    -> "handle-request", "Refresh Token Rotation" -> "refresh-token-rotation".
    """
    split = re.sub(r"(?<!^)(?=[A-Z])", "-", name)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", split).strip("-").lower()
    return slug or "boundary"


def _host_language(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "json": "json",
        "md": "markdown",
        "mdx": "markdown",
        "markdown": "markdown",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "ts": "typescript",
        "js": "javascript",
    }.get(suffix, "python")


def resolve_exercised(store, requirement: str | None) -> str | None:
    """The unique implementation satisfying the requirement, if unambiguous."""
    if requirement is None:
        return None
    candidates: list[str] = []
    for node in store.all_nodes(active_only=True):
        if node.node_type != "implementation":
            continue
        for edge in store.edges_from(node.entity_uid, "satisfies"):
            target = store.get_node(uid=edge.to_uid)
            if target is not None and target.trace_id == requirement:
                candidates.append(node.trace_id)
    return candidates[0] if len(candidates) == 1 else None


def _looks_like_test(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(
        p in ("tests", "test") or p.startswith("test_") or p.endswith("_test") for p in parts
    ) or bool(re.search(r"(^|/)(test_|.*_test\.)", path))


# trace:v1 id=impl.discovery.role-classifier work=WORK-TL-001
def classify_role(boundary: Boundary, path: str) -> str:
    """Artifact role of a boundary (impl/test/doc/ops/requirement/decision/plan)."""
    if boundary.language == "markdown":
        token = boundary.name.split(None, 1)[0] if boundary.name else ""
        prefix = token.split("-", 1)[0].upper()
        if prefix in ("REQ", "PRD"):
            return "requirement"
        if prefix in ("ADR", "DEC"):
            return "decision"
        if prefix == "PLAN":
            return "plan"
        return "doc"
    if boundary.language in ("yaml", "toml", "json"):
        return "ops"
    if boundary.kind in ("function", "method", "class"):
        return "test" if _looks_like_test(path) else "impl"
    return "impl"


# trace:v1 id=impl.discovery.suggest work=WORK-TL-001
def suggest_marker(
    boundary: Boundary,
    path: str,
    *,
    work: str | None = None,
    requirement: str | None = None,
    plan: str | None = None,
    exercised: str | None = None,
) -> MarkerSuggestion:
    """The canonical marker for a boundary under the given session context."""
    role = classify_role(boundary, path)
    syntax = _COMMENT_STYLES.get(boundary.language, "#")
    prefix = f"id={role}.{_slug(boundary.name)}"
    rel: list[str] = []
    if work:
        rel.append(f"work={work}")
    if role in ("impl", "test"):
        if requirement:
            rel.append(f"satisfies={requirement}" if role != "test" else f"verifies={requirement}")
    if role == "test":
        if exercised:
            rel.append(f"exercises={exercised}")

    if role == "doc" and requirement:
        rel.append(f"documents={requirement}")

    if role == "ops" and requirement:
        rel.append(f"satisfies={requirement}")
    if role == "impl" and plan:
        rel.append(f"implements={plan}")
    rel = [r for r in rel if r]
    marker = f"trace:v1 {prefix} {' '.join(rel)}".strip()
    sidecar = None
    note = ""
    if syntax is None:
        sidecar = f".trace/sidecars/{path}.json"
        record = {
            "line": boundary.start_line,
            "key": boundary.name,
            "marker": f"# {marker}",
        }
        note = (
            "JSON cannot carry comments; record the marker in the sidecar "
            f"({sidecar}) as {json.dumps(record, sort_keys=True)}"
        )
        return MarkerSuggestion(
            marker=marker,
            role=role,
            syntax="sidecar",
            sidecar=sidecar,
            note=note,
            relationships=rel,
        )
    comment = syntax
    if syntax == "<!-- -->":
        comment = "<!--"
    return MarkerSuggestion(
        marker=f"{comment} {marker}" + (" -->" if syntax == "<!-- -->" else ""),
        role=role,
        syntax=syntax,
        note=note,
        relationships=rel,
    )
