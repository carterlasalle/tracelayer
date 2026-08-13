"""trace:v1 marker grammar, parser, ID rules, ontology, and generated schema."""

from __future__ import annotations

from tracelayer.protocol.grammar import PREFIX, FieldToken, extract_marker_payload, quote_value
from tracelayer.protocol.ids import generate_id, infer_node_type, is_valid_id
from tracelayer.protocol.marker import (
    MarkerHit,
    MarkerParseResult,
    ParsedMarker,
    iter_marker_hits,
    parse_marker_hit,
    parse_marker_line,
    render_marker,
)
from tracelayer.protocol.ontology import (
    EDGE_ORDER,
    EDGE_TYPES,
    NODE_CATEGORIES,
    NODE_TYPES,
    OBSERVED_EDGES,
    SEMANTIC_EDGES,
    STRUCTURAL_EDGES,
)
from tracelayer.protocol.schema import (
    markdown_docs,
    marker_json_schema,
    marker_protocol_markdown,
    relationships_markdown,
)

__all__ = [
    "PREFIX",
    "FieldToken",
    "extract_marker_payload",
    "quote_value",
    "generate_id",
    "infer_node_type",
    "is_valid_id",
    "MarkerHit",
    "MarkerParseResult",
    "ParsedMarker",
    "iter_marker_hits",
    "parse_marker_hit",
    "parse_marker_line",
    "render_marker",
    "EDGE_ORDER",
    "EDGE_TYPES",
    "NODE_CATEGORIES",
    "NODE_TYPES",
    "OBSERVED_EDGES",
    "SEMANTIC_EDGES",
    "STRUCTURAL_EDGES",
    "marker_json_schema",
    "markdown_docs",
    "marker_protocol_markdown",
    "relationships_markdown",
]
