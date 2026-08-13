"""Unit tests for generated docs and JSON Schema (spec 35.3, 21.5).

Covers marker_json_schema() validity, markdown_docs() keys, and that the
generated tables contain every registry entry.
"""

from __future__ import annotations

import re

from tracelayer.protocol import (
    NODE_TYPES,
    OBSERVED_EDGES,
    SEMANTIC_EDGES,
    STRUCTURAL_EDGES,
    ids,
    markdown_docs,
    marker_json_schema,
)
from tracelayer.protocol.schema import GENERATED_HEADER


def test_json_schema_top_level() -> None:
    schema = marker_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "trace:v1 marker"
    assert schema["type"] == "object"
    assert schema["required"] == ["id"]
    assert schema["additionalProperties"] is False


def test_json_schema_type_enum_matches_registry() -> None:
    schema = marker_json_schema()
    assert schema["properties"]["type"]["enum"] == sorted(NODE_TYPES)


def test_json_schema_builtin_properties() -> None:
    schema = marker_json_schema()
    props = schema["properties"]
    assert set(props) >= {"id", "type", "title", "policy", "work", "plan"}
    assert props["id"]["type"] == "string"
    assert props["id"]["pattern"] == ids.ID_PATTERN.pattern
    assert re.compile(props["id"]["pattern"]).match("REQ-1") is not None
    assert re.compile(props["id"]["pattern"]).match("bad id") is None


def test_json_schema_contains_all_semantic_edges() -> None:
    schema = marker_json_schema()
    props = schema["properties"]
    for edge in sorted(SEMANTIC_EDGES):
        assert edge in props
        assert props[edge]["type"] == "string"


def test_json_schema_excludes_derived_edges() -> None:
    schema = marker_json_schema()
    props = schema["properties"]
    for edge in STRUCTURAL_EDGES | OBSERVED_EDGES:
        assert edge not in props


def test_markdown_docs_keys() -> None:
    docs = markdown_docs()
    assert set(docs) == {
        "docs/marker-protocol.md",
        "docs/relationships.md",
        "skills/traceability/marker-protocol.md",
    }


def test_marker_doc_contains_all_node_types() -> None:
    doc = markdown_docs()["docs/marker-protocol.md"]
    for name in sorted(NODE_TYPES):
        assert f"| `{name}` |" in doc


def test_relationships_doc_contains_all_edges() -> None:
    doc = markdown_docs()["docs/relationships.md"]
    for name in sorted(SEMANTIC_EDGES | STRUCTURAL_EDGES | OBSERVED_EDGES):
        assert f"| `{name}` |" in doc


def test_relationships_doc_kind_sections() -> None:
    doc = markdown_docs()["docs/relationships.md"]
    for kind in ("Semantic", "Structural", "Observed"):
        assert f"## {kind} edges" in doc


def test_generated_header_present() -> None:
    for content in markdown_docs().values():
        assert content.startswith(GENERATED_HEADER.rstrip("\n"))
