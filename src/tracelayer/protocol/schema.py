"""Generated documentation and JSON Schema from the protocol registries (35.3, 21.5).

`markdown_docs()` returns the full content of every generated documentation
file. `trace docs generate` writes them; `trace docs generate --check` fails
when files on disk drift from the registries.
"""

from __future__ import annotations

from tracelayer.protocol import ids, ontology

GENERATED_HEADER = (
    "<!-- GENERATED from protocol registries. Do not edit by hand; "
    "run `trace docs generate`. -->\n\n"
)


def marker_json_schema() -> dict:
    """JSON Schema for a parsed canonical marker."""
    edge_names = list(ontology.SEMANTIC_EDGES)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "trace:v1 marker",
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {
                "type": "string",
                "pattern": ids.ID_PATTERN.pattern,
                "description": "Stable trace identity; case-sensitive, unique per repository.",
            },
            "type": {
                "type": "string",
                "enum": sorted(ontology.NODE_TYPES),
                "description": "Optional artifact type; inferred from the ID namespace when absent.",
            },
            "title": {
                "type": "string",
                "description": "Optional descriptive metadata; not a graph edge.",
            },
            "policy": {"type": "string", "description": "Rare policy override reference."},
            "expects": {
                "type": "string",
                "description": "Plan-only: comma-separated artifact IDs the plan commits to producing.",
            },
            "work": {
                "type": "string",
                "description": "Convenience key; a `work` edge (comma-separated).",
            },
            "plan": {
                "type": "string",
                "description": "Convenience key; alias for `implements` (comma-separated).",
            },
            **{
                e: {
                    "type": "string",
                    "description": ontology.EDGE_TYPES[e].description,
                }
                for e in sorted(edge_names)
            },
        },
        "additionalProperties": False,
    }


def _node_types_markdown() -> str:
    lines = ["# Artifact Types", "", "| Type | Category | Description |", "|---|---|---|"]
    for name in sorted(ontology.NODE_TYPES):
        t = ontology.NODE_TYPES[name]
        lines.append(f"| `{t.name}` | {t.category} | {t.description} |")
    return "\n".join(lines)


def _edge_types_markdown() -> str:
    lines = ["# Edge Types", ""]
    for kind in ("semantic", "structural", "observed"):
        lines += [
            f"## {kind.title()} edges",
            "",
            "| Edge | Meaning | Typical source -> target |",
            "|---|---|---|",
        ]
        for name in ontology.EDGE_ORDER:
            e = ontology.EDGE_TYPES[name]
            if e.kind != kind:
                continue
            lines.append(f"| `{e.name}` | {e.description} | {e.typical} |")
        lines.append("")
    return "\n".join(lines)


# trace:v1 id=impl.protocol.schema-docs work=WORK-TL-001
def marker_protocol_markdown() -> str:
    """Full content of the normative marker protocol document."""
    content = [
        GENERATED_HEADER.rstrip(),
        "# Marker Protocol",
        "",
        "The canonical marker format is a single line, grep-friendly via `rg 'trace:v1'`:",
        "",
        "```text",
        "trace:v1 <key>=<value> <key>=<value> ...",
        "```",
        "",
        "Node-defining markers require `id=<trace-id>`.",
        "",
        "## Value encoding",
        "",
        "- Unquoted values may contain `[A-Za-z0-9._:/#@,+-]`.",
        "- Values containing whitespace MUST use double quotes.",
        '- Backslash escapes `\\`, `"`, `\\n`, `\\t` inside quoted values.',
        "- Repeated relations use comma-separated target IDs with no semantic",
        "  ordering unless the relation specifies one.",
        "- Empty values are invalid in canonical v1.",
        "- Duplicate keys on one marker are invalid.",
        "",
        "## Built-in properties",
        "",
        "- `id` — stable trace identity (required).",
        "- `type` — optional artifact type; inferred from the ID namespace when absent.",
        "- `title` — optional descriptive metadata; not a graph edge.",
        "- `policy` — rare policy override reference, not an arbitrary exemption.",
        "- `expects` — plan-only: comma-separated artifact IDs the plan commits",
        "  to producing; TL014 enforces each exists and links back via `implements`.",
        "",
        "## Convenience keys",
        "",
        "- `work` — a `work` edge (comma-separated targets).",
        "- `plan` — alias for the `implements` edge (comma-separated targets).",
        "",
        "Everything representing another artifact is an edge, not a generic path field.",
        "",
        "## Canonical examples",
        "",
        "```python",
        "# trace:v1 id=impl.auth.refresh work=AUTH-237 satisfies=REQ-AUTH-017 plan=PLAN-AUTH-237/P3",
        "def rotate_refresh_token(...):",
        "    ...",
        "```",
        "",
        "```python",
        "# trace:v1 id=test.auth.refresh-reuse verifies=REQ-AUTH-017 exercises=impl.auth.refresh",
        "def test_reused_refresh_token_is_rejected():",
        "    ...",
        "```",
        "",
        "```markdown",
        "<!-- trace:v1 id=ADR-0042 addresses=REQ-AUTH-017 supersedes=ADR-0021 -->",
        "```",
        "",
        "## Marker placement",
        "",
        "Markers MUST be adjacent to the behavior/artifact they define. Create",
        "markers at meaningful behavior boundaries (public API endpoints, business",
        "rules, security boundaries, contractual config, verification tests). Do",
        "NOT trace imports, trivial getters/setters, local loops, formatting",
        "changes, or generated code.",
        "",
        _node_types_markdown(),
        "",
        "## Derived facts are never declared",
        "",
        "Paths, line numbers, commit SHAs, authors, and test results are derived",
        "by the engine. Declaring structural (`calls`, ...) or observed",
        "(`executed`, `passed`, ...) relationships in a marker is an error.",
    ]
    return "\n".join(content) + "\n"


def relationships_markdown() -> str:
    """Full content of the edge semantics document."""
    return GENERATED_HEADER.rstrip() + "\n\n" + _edge_types_markdown() + "\n"


def markdown_docs() -> dict[str, str]:
    """Map of relative repo paths to generated documentation content."""
    return {
        "docs/marker-protocol.md": marker_protocol_markdown(),
        "docs/relationships.md": relationships_markdown(),
        "skills/traceability/references/marker-protocol.md": marker_protocol_markdown(),
    }
