"""Behavior-boundary extraction (review P1: TL013 + authoring classifiers).

A *behavioral boundary* is a place where trace-worthy behavior lives:
functions/classes/methods in code, headings in Markdown, top-level keys in
config files. Both the pre-edit authoring gate and the TL013 policy rule
use this module so the two layers classify identically.

``extract_boundaries(path, text)`` returns deterministic, ordered
boundaries; ``boundary_is_traced(text, boundaries, boundary)`` answers
whether the boundary is trace-accounted (marker attached, inherited from a
traced parent, explicitly exempted, or — for Markdown headings — a
node-inferring id token).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EXT_LANG = {
    "py": "python",
    "ts": "typescript",
    "js": "javascript",
    "go": "go",
    "rs": "rust",
    "java": "java",
}
_CONFIG_EXTS = {"yaml", "yml", "toml", "json"}
_DOC_EXTS = {"md", "mdx", "markdown"}
# Per-language exemption comments. An exemption is only honored with a
# machine-readable reason: ``# trace:exempt reason=<why>`` (auditable, no
# silent "just exempt it" path through the gate).
_EXEMPT_MARKERS = {
    "python": "# trace:exempt reason=",
    "yaml": "# trace:exempt reason=",
    "toml": "# trace:exempt reason=",
    "go": "// trace:exempt reason=",
    "rust": "// trace:exempt reason=",
    "java": "// trace:exempt reason=",
    "typescript": "// trace:exempt reason=",
    "javascript": "// trace:exempt reason=",
    "markdown": "<!-- trace:exempt reason=",
}


# trace:exempt reason=data container, no behavior  # data container, no behavior
@dataclass
class Boundary:
    """One behavioral boundary in a file."""

    name: str
    kind: str  # function | class | method | heading | config-key
    start_line: int  # 1-based
    end_line: int
    source: str
    language: str
    path: str = ""
    qualified_name: str = ""


# trace:exempt reason=public predicate helper, no behavior of its own  # public predicate helper, no behavior of its own
def supported_extension(path: str) -> bool:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return suffix in _EXT_LANG or suffix in _CONFIG_EXTS or suffix in _DOC_EXTS


# trace:v1 id=impl.policy.boundaries work=WORK-TL-001
def extract_boundaries(path: str, text: str) -> list[Boundary]:
    """All behavioral boundaries in ``text``, in line order (deterministic)."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix in _EXT_LANG:
        return _code_boundaries(_EXT_LANG[suffix], path, text)
    if suffix in _CONFIG_EXTS:
        return _config_boundaries(suffix, path, text)
    if suffix in _DOC_EXTS:
        return _markdown_boundaries(path, text)
    return []


# trace:v1 id=impl.policy.boundary-traced work=WORK-TL-001
def boundary_is_traced(
    text: str,
    boundaries: list[Boundary],
    boundary: Boundary,
    root: Path | None = None,
    store: object | None = None,
) -> bool:
    """True when the boundary is trace-accounted by any mechanism.

    Placement is exact: a marker only counts when it is ATTACHED to the
    boundary the way the indexer attaches markers — immediately above the
    definition with a bounded gap of blank/comment/decorator lines (for
    Markdown headings, in the marker window directly below the heading).
    A marker anywhere inside the body is not a trace of that boundary.

    No naming shortcuts: private helpers are not automatically traced;
    inheritance must be declared explicitly with a target that resolves to
    an active node in the same file.
    """
    lines = text.splitlines()
    if _exempt(lines, boundary):
        return True
    if _marker_attached(lines, boundary):
        return True
    if boundary.language == "markdown" and _heading_is_node(boundary):
        return True  # id-token heading infers a node without a marker
    if boundary.language in ("yaml", "toml") and _config_key_traced(lines, boundary):
        return True  # a marker directly above the key attaches to it
    if boundary.language == "json" and _json_sidecar_traced(boundary, root):
        return True  # JSON uses the sidecar anchor (.trace/sidecars/<path>.json)
    # Explicit inheritance declaration with a validated target.
    if store is not None and _inherit_valid(lines, boundary, store):
        return True
    return False


def _config_key_traced(lines: list[str], boundary: Boundary) -> bool:
    """A config-key boundary is traced by a marker directly above it."""
    for i in range(max(0, boundary.start_line - 2), boundary.start_line):
        if "trace:v1" in lines[i]:
            return True
    return False


# trace:exempt reason=internal-helper
def _json_sidecar_traced(boundary: Boundary, root: Path | None) -> bool:
    """JSON config keys are traced via the sidecar anchor (no comments)."""
    if root is None or not boundary.path:
        return False
    try:
        import json as _json

        sidecar = root / ".trace" / "sidecars" / f"{boundary.path}.json"
        data = _json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for entry in data.get("markers", []):
        if (
            entry.get("key") == boundary.name
            and abs(int(entry.get("line", 0)) - boundary.start_line) <= 1
        ):
            return True
    return False


# trace:v1 id=impl.policy.boundary-model work=WORK-TL-001
def _code_boundaries(language: str, path: str, text: str) -> list[Boundary]:
    try:
        from tracelayer.symbols.registry import get_parser

        parser = get_parser(language)
        symbols = parser.parse(text, path)
    except Exception:
        return []
    out: list[Boundary] = []
    for sym in symbols:
        name = getattr(sym, "name", "?")
        out.append(
            Boundary(
                name=str(name),
                kind=getattr(sym, "kind", "function") or "function",
                start_line=sym.start_line,
                end_line=sym.end_line,
                source=sym.source,
                language=language,
                path=path,
                qualified_name=getattr(sym, "qualified_name", "") or name,
            )
        )
    return out


# trace:exempt reason=internal-helper
def _config_boundaries(ext: str, path: str, text: str) -> list[Boundary]:
    """Top-level keys of a config file are boundaries (contracts)."""
    out: list[Boundary] = []
    if ext == "json":
        return _json_boundaries(path, text)
    lines = text.splitlines()
    indent = None
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "[", "---")):
            continue
        if ext == "toml" and line.startswith("["):
            out.append(Boundary(line.strip("[]"), "config-key", i, i, raw, ext, path))
            continue
        if ":" not in line and "=" not in line:
            continue
        key = line.split(":", 1)[0].split("=", 1)[0].strip().strip("\"'")
        cur_indent = len(raw) - len(raw.lstrip())
        if indent is None:
            indent = cur_indent
        if cur_indent > indent:
            continue  # nested keys are covered by the parent boundary
        out.append(Boundary(key, "config-key", i, i, raw, ext, path))
    return out


# trace:exempt reason=internal-helper
def _json_boundaries(path: str, text: str) -> list[Boundary]:
    try:
        import json

        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out: list[Boundary] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        for key in data:
            if stripped.startswith(f'"{key}"') or stripped.startswith(f"'{key}'"):
                out.append(Boundary(str(key), "config-key", i, i, raw, "json", path))
    return out


# trace:exempt reason=internal-helper
def _markdown_boundaries(path: str, text: str) -> list[Boundary]:
    """Headings are boundaries; body extends to the next same-or-higher heading."""
    import re

    atx = re.compile(r"^(#{1,6})\s+(.*)$")
    lines = text.splitlines()
    out: list[Boundary] = []
    headings: list[tuple[int, int, str, str]] = []  # line, level, title, raw
    for i, raw in enumerate(lines, start=1):
        m = atx.match(raw)
        if m is not None:
            headings.append((i, len(m.group(1)), m.group(2).strip(), raw))
    for idx, (line, level, title, raw) in enumerate(headings):
        end = len(lines)
        for nxt_line, nxt_level, _, _ in headings[idx + 1 :]:
            if nxt_level <= level:
                end = nxt_line - 1
                break
        body = "\n".join(lines[line : end + 1]) if line <= end else raw
        out.append(Boundary(title, "heading", line, end, body, "markdown", path))
    return out


def _heading_is_node(boundary: Boundary) -> bool:
    """True when the heading's first token infers a node type (REQ-/ADR-/...)."""
    import re

    from tracelayer.protocol.ids import infer_node_type

    token = boundary.name.split(None, 1)[0] if boundary.name else ""
    if not re.match(r"^[A-Za-z0-9._:/-]+$", token):
        return False
    return infer_node_type(token) is not None


# trace:exempt reason=internal-helper
def _exempt(lines: list[str], boundary: Boundary) -> bool:
    """Explicit exemption directly above the boundary, with a reason.

    The exemption must carry ``reason=<why>``: bare ``trace:exempt`` is
    ignored so agents cannot shortcut the gate without an auditable cause.
    """
    marker = _EXEMPT_MARKERS.get(boundary.language)
    if marker is None:
        return False
    for i in range(max(0, boundary.start_line - 2), boundary.start_line):
        line = lines[i]
        if marker in line and line.split("reason=", 1)[1].strip():
            return True
    return False


# trace:exempt reason=internal-helper
def _inherit_target(lines: list[str], boundary: Boundary) -> str | None:
    """The declared inheritance target id, or None.

    ``// trace:inherit <trace-id> reason=<why>`` directly above the
    boundary. The target must resolve to an active node in the same file
    (checked by the caller against the store); a bare or unresolvable
    declaration is not accounting.
    """
    import re

    language_marker = {
        "python": "#",
        "yaml": "#",
        "toml": "#",
        "go": "//",
        "rust": "//",
        "java": "//",
        "typescript": "//",
        "javascript": "//",
        "markdown": "<!--",
    }.get(boundary.language)
    if language_marker is None:
        return None
    for i in range(max(0, boundary.start_line - 2), boundary.start_line):
        line = lines[i].strip()
        if language_marker not in line or "trace:inherit" not in line or "reason=" not in line:
            continue
        m = re.search(r"trace:inherit\s+([A-Za-z0-9._:/-]+)", line)
        if m is not None:
            return m.group(1)
    return None


# trace:exempt reason=internal-helper
def _inherit_valid(lines: list[str], boundary: Boundary, store: Any) -> bool:
    """Inheritance counts only when the target exists, is active, and is an
    enclosing parent in the same file."""
    target_id = _inherit_target(lines, boundary)
    if target_id is None:
        return False
    try:
        target = store.get_node(trace_id=target_id)
    except Exception:
        return False
    if target is None or not target.active:
        return False
    if target.canonical_path != boundary.path:
        return False  # a legitimate enclosing/semantic parent lives in the same file
    if target.source_start_line and target.source_start_line > boundary.start_line:
        return False  # not enclosing: starts below the child
    return True


_COMMENT_PREFIX = {
    "python": ("#", "@"),
    "yaml": ("#",),
    "toml": ("#",),
    "go": ("//", "/*", "*"),
    "rust": ("//", "/*", "*"),
    "java": ("//", "/*", "*"),
    "typescript": ("//", "/*", "*"),
    "javascript": ("//", "/*", "*"),
    "markdown": ("<!--",),
}
_MAX_GAP = 3  # mirrors the indexer's marker->symbol attachment window


# trace:exempt reason=internal-helper
def _marker_attached(lines: list[str], boundary: Boundary) -> bool:
    """The indexer's attachment rule, not a body scan.

    Code/config: a marker within ``_MAX_GAP`` lines above the boundary
    start, with only blank/comment/decorator lines in between. Markdown
    headings attach the marker window directly below the heading (the
    indexer absorbs those markers into the block).
    """
    if boundary.language == "markdown":
        for i in range(boundary.start_line, min(len(lines), boundary.start_line + 6)):
            if "trace:v1" in lines[i]:
                return True
        return False
    prefixes = _COMMENT_PREFIX.get(boundary.language, ("#",))
    for marker_idx in range(max(0, boundary.start_line - 1 - _MAX_GAP), boundary.start_line):
        if "trace:v1" not in lines[marker_idx]:
            continue
        if _gap_ok(lines, marker_idx + 1, boundary.start_line - 1, prefixes):
            return True
    return False


# trace:exempt reason=internal-helper
def _gap_ok(lines: list[str], start: int, end: int, prefixes: tuple[str, ...]) -> bool:
    for i in range(start, end):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if any(stripped.startswith(p) for p in prefixes):
            continue
        return False
    return True
