"""Markdown artifact extraction: headings, HTML-comment markers, fences.

Acceptance-relevant behavior (documented simplifications):

- Code fences (````` ``` ````, ``~~~``, up to 3 leading spaces) and indented
  code blocks exclude markers from the marker scan. Indented-code detection is
  a simplified CommonMark: a line indented >= 4 columns is code when the
  previous non-blank line is also indented, or when the previous line is blank
  or the file starts (indented blocks cannot interrupt paragraphs). Tabs count
  as 4 columns. Deep list-item continuation edge cases may misclassify;
  fences themselves are exact.
- Heading ID inference: ATX heading whose first content token matches
  ``[A-Za-z0-9._:/-]+`` and whose namespace infers a node type, e.g.
  ``## REQ-AUTH-017 - Refresh token rotation`` -> requirement ``REQ-AUTH-017``.
  The raw token is the trace ID (a trailing ``:`` stays part of the ID).
- Body = raw lines until the next ATX heading of the same or higher level;
  headings inside code fences do not end a body.
- Marker blocks: ``<!-- trace:v1 ... -->`` comment markers (opener on the
  marker's own line) outside fences create nodes via ``parse_marker_hit``.
  A marker within 5 lines after a heading whose parsed trace_id equals the
  heading ID contributes its edges to that block instead of creating its own
  block (marker node/block dedupe is the caller's job, as with plain markers).
  Multi-line HTML comments whose marker line lacks the ``<!--`` opener are
  found by the plain marker scan but do not produce artifact blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tracelayer.config import TraceConfig
from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint
from tracelayer.protocol import (
    MarkerHit,
    ParsedMarker,
    infer_node_type,
    iter_marker_hits,
    parse_marker_hit,
)

_ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# A marker found within this many lines after a heading (with a matching ID)
# contributes its edges to the heading block (spec 11.4).
EDGE_WINDOW_LINES = 5


@dataclass
class MarkdownBlock:
    trace_id: str
    node_type: str
    title: str
    body: str
    fingerprint: str  # semantic_fingerprint(normalize_block(body)); callers
    # must pass the value computed from `body` for the invariant to hold.
    path: str
    line: int  # 1-based heading (or marker) line
    edges: dict[str, list[str]] = field(default_factory=dict)


def _code_line_flags(lines: list[str]) -> list[bool]:
    """True for lines inside fenced or indented code blocks (see module doc)."""
    flags = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    prev_indented: bool | None = None  # previous non-blank line indented >= 4?
    prev_blank = True  # file start behaves like a blank line
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence_char is not None:
            flags[i] = True
            if (
                m is not None
                and m.group(1)[0] == fence_char
                and len(m.group(1)) >= fence_len
                and not line[m.end() :].strip()
            ):
                fence_char = None
            prev_blank, prev_indented = False, False
            continue
        if m is not None:
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            flags[i] = True
            prev_blank, prev_indented = False, False
            continue
        if not line.strip():
            prev_blank = True
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        cols = sum(4 if c == "\t" else 1 for c in line[:indent])
        if cols >= 4 and (prev_indented is None or prev_indented or prev_blank):
            flags[i] = True
        prev_indented, prev_blank = cols >= 4, False
    return flags


def markdown_marker_hits(path: str, text: str) -> list[MarkerHit]:
    """Marker hits outside fenced/indented code blocks, in line order."""
    flags = _code_line_flags(text.splitlines())
    return [
        hit
        for hit in iter_marker_hits(text, path)
        if hit.line <= len(flags) and not flags[hit.line - 1]
    ]


def _ends_section(line: str, level: int) -> bool:
    m = _ATX_RE.match(line)
    return m is not None and len(m.group(1)) <= level


def _comment_body(raw: str) -> str:
    body = raw.strip()
    if body.startswith("<!--"):
        body = body[4:]
    if body.endswith("-->"):
        body = body[:-3]
    return body.strip()


def extract_markdown_blocks(
    path: str, text: str, config: TraceConfig
) -> list[MarkdownBlock]:
    """Extract artifact blocks from markdown headings and comment markers.

    See the module docstring for exact semantics. Blocks are returned in line
    order; ``config.markers.unknown_keys`` controls marker parsing strictness.
    """
    lines = text.splitlines()
    flags = _code_line_flags(lines)
    parsed: dict[int, ParsedMarker] = {}
    for hit in markdown_marker_hits(path, text):
        result = parse_marker_hit(hit, unknown_keys=config.markers.unknown_keys)
        if result.marker is not None and result.marker.trace_id:
            parsed[hit.line] = result.marker

    blocks: list[MarkdownBlock] = []
    consumed: set[int] = set()  # marker lines absorbed into a heading block
    for i, line in enumerate(lines):
        if flags[i]:
            continue
        m = _ATX_RE.match(line)
        if m is None:
            continue
        level = len(m.group(1))
        content = m.group(2).strip()
        if not content:
            continue
        token = content.split(None, 1)[0]
        node_type = infer_node_type(token) if _ID_TOKEN_RE.match(token) else None
        if node_type is None:
            continue
        heading_no = i + 1
        end = i + 1
        while end < len(lines) and (flags[end] or not _ends_section(lines[end], level)):
            end += 1
        body = "\n".join(lines[i + 1 : end])
        edges: dict[str, list[str]] = {}
        for mline in sorted(parsed):
            if heading_no < mline <= heading_no + EDGE_WINDOW_LINES:
                marker = parsed[mline]
                if marker.trace_id == token:
                    consumed.add(mline)
                    for key, values in marker.edges.items():
                        merged = edges.setdefault(key, [])
                        for v in values:
                            if v not in merged:
                                merged.append(v)
        title = content[len(token) :].lstrip("-: \t").strip() or token
        blocks.append(
            MarkdownBlock(
                trace_id=token,
                node_type=node_type,
                title=title,
                body=body,
                fingerprint=semantic_fingerprint(normalize_block(body)),
                path=path,
                line=heading_no,
                edges=edges,
            )
        )

    for mline in sorted(parsed):
        if mline in consumed:
            continue
        marker = parsed[mline]
        if marker.trace_id is None or not marker.raw.strip().startswith("<!--"):
            continue
        body = _comment_body(marker.raw)
        node_type = marker.node_type or infer_node_type(marker.trace_id) or "document"
        blocks.append(
            MarkdownBlock(
                trace_id=marker.trace_id,
                node_type=node_type,
                title=marker.title or marker.trace_id,
                body=body,
                fingerprint=semantic_fingerprint(normalize_block(body)),
                path=path,
                line=mline,
                edges=dict(marker.edges),
            )
        )

    return sorted(blocks, key=lambda b: b.line)
