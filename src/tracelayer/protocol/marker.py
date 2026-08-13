"""Canonical marker parser (FR-001, spec Section 11).

Parses single-line `trace:v1 key=value ...` markers from supported comment
syntaxes into ParsedMarker objects with typed semantic edges.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from tracelayer.diagnostics import Diagnostic, make
from tracelayer.protocol import grammar, ids, ontology

BUILTIN_PROPERTIES = frozenset({"id", "type", "title", "policy"})

# Convenience relation-like keys (spec 11.3, 33.1): `work` and `plan` are
# graph edges; `plan` is an alias for `implements`.
CONVENIENCE_EDGES: dict[str, str] = {"work": "work", "plan": "implements"}


@dataclass
class MarkerHit:
    path: str
    line: int  # 1-based
    column: int  # 1-based
    raw: str  # full original line
    payload: str  # substring starting at `trace:v1`


@dataclass
class ParsedMarker:
    path: str
    line: int
    column: int
    raw: str
    trace_id: str | None = None
    node_type: str | None = None
    title: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class MarkerParseResult:
    marker: ParsedMarker | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # Noncanonical keys preserved under permissive/migration mode.
    migrated: dict[str, str] = field(default_factory=dict)


def iter_marker_hits(text: str, path: str) -> Iterator[MarkerHit]:
    """Yield every marker line in `text`. Line-based; Markdown callers filter
    fenced code blocks before calling this."""
    for i, line in enumerate(text.splitlines(), start=1):
        payload = grammar.extract_marker_payload(line)
        if payload is not None:
            yield MarkerHit(
                path=path,
                line=i,
                column=line.find(grammar.PREFIX) + 1,
                raw=line,
                payload=payload,
            )


def parse_marker_line(
    line: str,
    path: str = "<string>",
    line_no: int = 1,
    *,
    unknown_keys: str = "error",
) -> MarkerParseResult:
    """Parse a single line. Returns an empty result when the line is not a marker."""
    payload = grammar.extract_marker_payload(line)
    if payload is None:
        return MarkerParseResult(marker=None)
    return _parse_payload(
        payload,
        raw=line,
        path=path,
        line=line_no,
        column=line.find(grammar.PREFIX) + 1,
        unknown_keys=unknown_keys,
    )


def parse_marker_hit(hit: MarkerHit, *, unknown_keys: str = "error") -> MarkerParseResult:
    return _parse_payload(
        hit.payload,
        raw=hit.raw,
        path=hit.path,
        line=hit.line,
        column=hit.column,
        unknown_keys=unknown_keys,
    )


def _parse_payload(
    payload: str, *, raw: str, path: str, line: int, column: int, unknown_keys: str
) -> MarkerParseResult:
    tokens, diags = grammar.tokenize_fields(payload, path=path, line=line)
    marker = ParsedMarker(path=path, line=line, column=column, raw=raw)
    result = MarkerParseResult(marker=marker)
    seen: set[str] = set()
    for tok in tokens:
        if tok.key in seen:
            diags.append(
                make(
                    "TL006",
                    path=path,
                    line=line,
                    message=f"Duplicate key {tok.key!r} on one marker",
                )
            )
            continue
        seen.add(tok.key)
        if not tok.value:
            diags.append(
                make(
                    "TL004",
                    path=path,
                    line=line,
                    message=f"Empty value for key {tok.key!r}; empty values are invalid in canonical v1",
                )
            )
            continue
        if tok.key == "id":
            marker.trace_id = tok.value
            if not ids.is_valid_id(tok.value):
                diags.append(
                    make("TL005", path=path, line=line, message=f"Invalid trace ID {tok.value!r}")
                )
        elif tok.key == "type":
            marker.node_type = tok.value
            if tok.value not in ontology.NODE_TYPES:
                diags.append(
                    make(
                        "TL007",
                        path=path,
                        line=line,
                        message=f"Unknown artifact type {tok.value!r}",
                    )
                )
        elif tok.key == "title":
            marker.title = tok.value
        elif tok.key == "policy":
            marker.properties["policy"] = tok.value
        elif tok.key in CONVENIENCE_EDGES:
            edge = CONVENIENCE_EDGES[tok.key]
            marker.edges.setdefault(edge, []).extend(_validated_targets(tok, path, line, diags))
        elif tok.key in ontology.SEMANTIC_EDGES:
            marker.edges.setdefault(tok.key, []).extend(_validated_targets(tok, path, line, diags))
        elif tok.key in ontology.STRUCTURAL_EDGES or tok.key in ontology.OBSERVED_EDGES:
            diags.append(
                make(
                    "TL040",
                    path=path,
                    line=line,
                    message=(
                        f"Key {tok.key!r} is a derived relationship; it is computed "
                        "by the engine and cannot be declared in a marker"
                    ),
                )
            )
        elif unknown_keys in ("permissive", "migration"):
            result.migrated[tok.key] = tok.value
            diags.append(
                make(
                    "TL040",
                    severity="INFO",
                    path=path,
                    line=line,
                    message=f"Unknown key {tok.key!r} preserved under permissive/migration mode",
                )
            )
        else:
            severity = "WARNING" if unknown_keys == "warning" else "ERROR"
            diags.append(
                make(
                    "TL040",
                    severity=severity,
                    path=path,
                    line=line,
                    message=f"Unknown key {tok.key!r}",
                )
            )

    if marker.trace_id is None:
        diags.append(
            make(
                "TL004", path=path, line=line, message="Node-defining markers require id=<trace-id>"
            )
        )
    else:
        inferred = ids.infer_node_type(marker.trace_id)
        if marker.node_type is not None and inferred is not None and marker.node_type != inferred:
            diags.append(
                make(
                    "TL007",
                    severity="INFO",
                    path=path,
                    line=line,
                    message=(
                        f"Explicit type {marker.node_type!r} differs from inferred "
                        f"type {inferred!r} for ID {marker.trace_id!r}"
                    ),
                )
            )
        if marker.node_type is None:
            marker.node_type = inferred

    result.diagnostics = diags
    return result


def _validated_targets(
    tok: grammar.FieldToken, path: str, line: int, diags: list[Diagnostic]
) -> list[str]:
    if tok.quoted:
        targets = [tok.value]
    else:
        targets = [t for t in tok.value.split(",") if t]
    for t in targets:
        if not ids.is_valid_id(t):
            diags.append(make("TL004", path=path, line=line, message=f"Invalid edge target {t!r}"))
    return targets


def render_marker(marker: ParsedMarker) -> str:
    """Render a ParsedMarker back to canonical one-line form (round-trip)."""
    parts = [grammar.PREFIX]
    if marker.trace_id is not None:
        parts.append(f"id={marker.trace_id}")
    if marker.node_type is not None:
        parts.append(f"type={marker.node_type}")
    if marker.title is not None:
        parts.append(f"title={grammar.quote_value(marker.title)}")
    if "policy" in marker.properties:
        parts.append(f"policy={grammar.quote_value(marker.properties['policy'])}")
    for edge in ontology.EDGE_ORDER:
        targets = marker.edges.get(edge)
        if targets:
            parts.append(f"{edge}={','.join(targets)}")
    return " ".join(parts)
