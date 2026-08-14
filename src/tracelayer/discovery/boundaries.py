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
_EXEMPT_MARKS = ("# trace:exempt", "<!-- trace:exempt -->")


# trace:exempt  # data container, no behavior
@dataclass
class Boundary:
    """One behavioral boundary in a file."""

    name: str
    kind: str  # function | class | method | heading | config-key
    start_line: int  # 1-based
    end_line: int
    source: str
    language: str


# trace:exempt  # public predicate helper, no behavior of its own
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
        return _config_boundaries(suffix, text)
    if suffix in _DOC_EXTS:
        return _markdown_boundaries(text)
    return []


def boundary_is_traced(text: str, boundaries: list[Boundary], boundary: Boundary) -> bool:
    """True when the boundary is trace-accounted by any mechanism."""
    lines = text.splitlines()
    if boundary.kind in ("function", "method") and boundary.name.startswith("_"):
        return True  # private helpers are internal by convention (not boundaries)
    if _exempt(lines, boundary):
        return True
    if _marker_in(lines, boundary.start_line, boundary.end_line):
        return True
    if boundary.language == "markdown" and _heading_is_node(boundary):
        return True  # id-token heading infers a node without a marker
    if boundary.language in ("yaml", "toml", "json") and any("trace:v1" in ln for ln in lines):
        return True  # config files are claimed at file level
    # Inherited: inside an already-traced parent boundary.
    for parent in boundaries:
        if parent is boundary:
            continue
        if parent.start_line <= boundary.start_line <= parent.end_line:
            if _marker_in(lines, parent.start_line, parent.end_line):
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
            )
        )
    return out


def _config_boundaries(ext: str, text: str) -> list[Boundary]:
    """Top-level keys of a config file are boundaries (contracts)."""
    out: list[Boundary] = []
    if ext == "json":
        return _json_boundaries(text)
    lines = text.splitlines()
    indent = None
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "[", "---")):
            continue
        if ext == "toml" and line.startswith("["):
            out.append(Boundary(line.strip("[]"), "config-key", i, i, raw, ext))
            continue
        if ":" not in line and "=" not in line:
            continue
        key = line.split(":", 1)[0].split("=", 1)[0].strip().strip("\"'")
        cur_indent = len(raw) - len(raw.lstrip())
        if indent is None:
            indent = cur_indent
        if cur_indent > indent:
            continue  # nested keys are covered by the parent boundary
        out.append(Boundary(key, "config-key", i, i, raw, ext))
    return out


def _json_boundaries(text: str) -> list[Boundary]:
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
                out.append(Boundary(str(key), "config-key", i, i, raw, "json"))
    return out


def _markdown_boundaries(text: str) -> list[Boundary]:
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
        out.append(Boundary(title, "heading", line, end, body, "markdown"))
    return out


def _heading_is_node(boundary: Boundary) -> bool:
    """True when the heading's first token infers a node type (REQ-/ADR-/...)."""
    import re

    from tracelayer.protocol.ids import infer_node_type

    token = boundary.name.split(None, 1)[0] if boundary.name else ""
    if not re.match(r"^[A-Za-z0-9._:/-]+$", token):
        return False
    return infer_node_type(token) is not None


def _exempt(lines: list[str], boundary: Boundary) -> bool:
    for i in range(max(0, boundary.start_line - 2), boundary.start_line):
        if any(mark in lines[i] for mark in _EXEMPT_MARKS):
            return True
    return False


def _marker_in(lines: list[str], start: int, end: int) -> bool:
    for i in range(max(0, start - 2), min(len(lines), end)):
        if "trace:v1" in lines[i]:
            return True
    return False
