"""Property-based tests for the marker protocol (spec 47.2).

Uses hypothesis: arbitrary valid markers round-trip through
render_marker/parse_marker_line; arbitrary junk lines never raise (they
return diagnostics or an empty result); arbitrary strings never crash
render_marker. `assume()` encodes validity preconditions (empty values and
non-ID edge targets are invalid in canonical v1).
"""

from __future__ import annotations

import string

from hypothesis import assume, given
from hypothesis import strategies as st

from tracelayer.protocol import (
    NODE_TYPES,
    SEMANTIC_EDGES,
    MarkerHit,
    ParsedMarker,
    grammar,
    ids,
    parse_marker_hit,
    parse_marker_line,
    render_marker,
)

ID_CHARS = string.ascii_letters + string.digits + "._:/-"
valid_id_text = st.text(alphabet=ID_CHARS, min_size=1)

marker_strategy = st.fixed_dictionaries(
    {
        "trace_id": valid_id_text,
        "node_type": st.sampled_from(sorted(NODE_TYPES)),
        "title": st.text(),
        "policy": st.text(),
        "edges": st.dictionaries(
            st.sampled_from(sorted(SEMANTIC_EDGES)),
            st.lists(valid_id_text, min_size=1, max_size=4),
            max_size=6,
        ),
    }
)


@given(fields=marker_strategy)
def test_round_trip_arbitrary_valid_marker(fields: dict) -> None:
    trace_id: str = fields["trace_id"]
    node_type: str = fields["node_type"]
    title: str = fields["title"]
    policy: str = fields["policy"]
    edges: dict[str, list[str]] = fields["edges"]

    # Validity preconditions for canonical v1 markers.
    assume(ids.is_valid_id(trace_id))
    assume(title != "")  # empty values are invalid
    assume(policy != "")
    for targets in edges.values():
        for target in targets:
            assume(ids.is_valid_id(target))

    marker = ParsedMarker(
        path="<prop>",
        line=1,
        column=1,
        raw="",
        trace_id=trace_id,
        node_type=node_type,
        title=title,
        properties={"policy": policy},
        edges=dict(edges),
    )
    rendered = render_marker(marker)
    result = parse_marker_line(rendered, path="<prop>")
    assert result.marker is not None
    assert result.marker.trace_id == trace_id
    assert result.marker.node_type == node_type
    assert result.marker.title == title
    assert result.marker.properties == {"policy": policy}
    assert result.marker.edges == edges


@given(fields=marker_strategy)
def test_render_is_idempotent(fields: dict) -> None:
    trace_id: str = fields["trace_id"]
    title: str = fields["title"]
    policy: str = fields["policy"]
    edges: dict[str, list[str]] = fields["edges"]
    assume(ids.is_valid_id(trace_id))
    assume(title != "")
    assume(policy != "")
    for targets in edges.values():
        for target in targets:
            assume(ids.is_valid_id(target))

    marker = ParsedMarker(
        path="<prop>",
        line=1,
        column=1,
        raw="",
        trace_id=trace_id,
        node_type=fields["node_type"],
        title=title,
        properties={"policy": policy},
        edges=dict(edges),
    )
    first = render_marker(marker)
    reparsed = parse_marker_line(first, path="<prop>")
    assert reparsed.marker is not None
    assert render_marker(reparsed.marker) == first


@given(line=st.text())
def test_arbitrary_junk_lines_never_raise(line: str) -> None:
    result = parse_marker_line(line)
    if result.marker is None:
        # Not a marker line: empty result, no diagnostics.
        assert result.diagnostics == []
        assert result.migrated == {}


@given(line=st.text())
def test_arbitrary_lines_parse_then_render_never_crashes(line: str) -> None:
    result = parse_marker_line(line)
    if result.marker is not None:
        render_marker(result.marker)  # must not raise


@given(title=st.text(), policy=st.text(), targets=st.lists(st.text(), max_size=4))
def test_render_marker_never_crashes(title: str, policy: str, targets: list[str]) -> None:
    marker = ParsedMarker(
        path="<p>",
        line=1,
        column=1,
        raw="",
        trace_id="REQ-1",
        node_type="requirement",
        title=title,
        properties={"policy": policy},
        edges={"satisfies": [t for t in targets if ids.is_valid_id(t)]},
    )
    render_marker(marker)


@given(value=st.text())
def test_quote_value_round_trip(value: str) -> None:
    quoted = grammar.quote_value(value)
    tokens, diags = grammar.tokenize_fields(f"trace:v1 k={quoted}", path="<s>", line=1)
    assert diags == []
    assert len(tokens) == 1
    assert tokens[0].value == value


@given(value=valid_id_text)
def test_arbitrary_valid_id_parses_clean(value: str) -> None:
    assume(ids.is_valid_id(value))
    result = parse_marker_line(f"trace:v1 id={value}")
    assert result.marker is not None
    assert result.marker.trace_id == value
    assert not any(d.severity == "ERROR" for d in result.diagnostics)


@given(payload=st.text())
def test_arbitrary_payload_hit_never_raises(payload: str) -> None:
    hit = MarkerHit(path="<p>", line=1, column=1, raw=payload, payload=payload)
    result = parse_marker_hit(hit)
    assert result is not None
    if result.marker is not None:
        render_marker(result.marker)
