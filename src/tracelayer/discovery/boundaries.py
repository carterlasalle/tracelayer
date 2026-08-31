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
    "pyi": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
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
    # Explicit inheritance declaration with a validated structural parent.
    if store is not None and _inherit_valid(lines, boundary, boundaries, store):
        return True
    return False


# trace:exempt reason=internal-helper
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
    """Top-level keys of a config file are boundaries (contracts).

    YAML/JSON multi-line values (list items, folded scalars) are not keys:
    only ``key:``/``key=`` lines at the file's base indent are boundaries,
    and a dangling ``-`` list marker without a key is ignored.
    """
    out: list[Boundary] = []
    if ext == "json":
        return _json_boundaries(path, text)
    lines = text.splitlines()
    indent = None
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "[", "---")):
            continue
        # A trailing comma changes the raw line but not the contract key:
        # strip it before comparing so reorderings with/without trailing
        # commas produce the same fingerprint (F5).
        norm = line.rstrip(",").rstrip()
        if ":" not in norm and "=" not in norm:
            continue
        key = norm.split(":", 1)[0].split("=", 1)[0].strip().strip("\"'")
        cur_indent = len(raw) - len(raw.lstrip())
        if indent is None:
            indent = cur_indent
        if cur_indent > indent:
            # nested key or list item under a parent key: covered by the
            # parent boundary (base indent governs what is "top-level").
            continue
        out.append(Boundary(key, "config-key", i, i, norm, ext, path))
    return out


# trace:exempt reason=internal-helper
def _json_boundaries(path: str, text: str) -> list[Boundary]:
    """Top-level JSON keys are boundaries; located by text position so
    single-line JSON works (the key's line = newlines before it + 1)."""
    try:
        import json

        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    lines = text.splitlines()
    out: list[Boundary] = []
    for key in data:
        idx = text.find(f'"{key}"')
        if idx < 0:
            continue
        line = text.count("\n", 0, idx) + 1
        raw = lines[line - 1] if line - 1 < len(lines) else ""
        out.append(Boundary(str(key), "config-key", line, line, raw, "json", path))
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


# trace:exempt reason=internal-helper
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
def _inherit_valid(
    lines: list[str], boundary: Boundary, boundaries: list[Boundary], store: Any
) -> bool:
    """Inheritance counts only when the target is the boundary's actual
    structural parent: the target node must exist, be active, live in the
    same file, and carry the marker attached to the narrowest boundary that
    encloses the child in the parsed file.

    A function defined earlier in the file is not a parent; a class whose
    marker traces it and whose range contains the method is.
    """
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
        return False
    enclosing = min(
        (
            b
            for b in boundaries
            if b is not boundary
            and b.start_line <= boundary.start_line
            and b.end_line >= boundary.end_line
        ),
        key=lambda b: (b.end_line - b.start_line, b.start_line),
        default=None,
    )
    if enclosing is None:
        return False  # no structural parent: a global cannot inherit
    attached = _attached_marker_id(lines, enclosing)
    return attached == target_id


# trace:exempt reason=internal-helper
def _attached_marker_id(lines: list[str], boundary: Boundary) -> str | None:
    """The trace id of the marker attached to a boundary (indexer placement)."""
    import re

    if boundary.language == "markdown":
        window = range(boundary.start_line, min(len(lines), boundary.start_line + 6))
    else:
        window = range(max(0, boundary.start_line - 1 - _MAX_GAP), boundary.start_line)
    for i in window:
        line = lines[i] if i < len(lines) else ""
        if "trace:v1" not in line:
            continue
        if boundary.language != "markdown" and i < boundary.start_line:
            if not _gap_ok(
                lines,
                i + 1,
                boundary.start_line - 1,
                _COMMENT_PREFIX.get(boundary.language, ("#",)),
            ):
                continue
        m = re.search(r"id=([A-Za-z0-9._:/-]+)", line)
        if m is not None:
            return m.group(1)
    return None


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
        # Unified with the indexer's absorption window
        # (artifacts.markdown.EDGE_WINDOW_LINES): a marker strictly below
        # the heading within that window, or directly above it, attaches.
        # The indexer's window is purely positional — no prose check —
        # and a checker stricter than the indexer is the divergence bug
        # this fixes (F4).
        from tracelayer.artifacts.markdown import EDGE_WINDOW_LINES

        heading_idx = boundary.start_line - 1
        candidates = [heading_idx - 1] + list(
            range(heading_idx + 1, min(len(lines), heading_idx + 1 + EDGE_WINDOW_LINES))
        )
        return any("trace:v1" in lines[i] for i in candidates if 0 <= i < len(lines))
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
