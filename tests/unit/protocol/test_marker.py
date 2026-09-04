"""Unit tests for the canonical marker parser (spec Section 11, FR-001).

Covers every built-in property (id/type/title/policy), convenience keys
(work=/plan=), duplicate keys (TL006), missing id (TL004), invalid id (TL005),
unknown-key modes (error/warning/permissive/migration), derived keys
(calls=/executed=) rejection, comma-separated targets, and render round-trips.
"""

from __future__ import annotations

import pytest

from tracelayer.diagnostics import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARNING
from tracelayer.protocol import (
    OBSERVED_EDGES,
    PREFIX,
    SEMANTIC_EDGES,
    STRUCTURAL_EDGES,
    MarkerHit,
    ParsedMarker,
    parse_marker_hit,
    parse_marker_line,
    render_marker,
)
from tracelayer.protocol.marker import (
    BUILTIN_PROPERTIES,
    CONVENIENCE_EDGES,
    iter_marker_hits,
)

# ---------------------------------------------------------------------------
# Built-in properties
# ---------------------------------------------------------------------------


# trace:v1 id=test.dogfood.tests.unit.protocol.test_marker.py type=test
def test_parse_simple_marker() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 type=requirement title=Auth")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.trace_id == "REQ-1"
    assert result.marker.node_type == "requirement"
    assert result.marker.title == "Auth"
    assert result.marker.properties == {}
    assert result.marker.edges == {}


def test_parse_inferred_type() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.node_type == "requirement"


def test_parse_policy_property() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 policy=POL-7")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.properties == {"policy": "POL-7"}


def test_parse_explicit_type_mismatch_info() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 type=test")
    assert result.marker is not None
    assert result.marker.node_type == "test"
    info = [d for d in result.diagnostics if d.rule_id == "TL007"]
    assert len(info) == 1
    assert info[0].severity == SEVERITY_INFO


def test_parse_unknown_type() -> None:
    result = parse_marker_line(f"{PREFIX} type=bogus")
    assert result.marker is not None
    assert result.marker.node_type == "bogus"
    errs = [d for d in result.diagnostics if d.rule_id == "TL007"]
    assert len(errs) == 1
    assert errs[0].severity == SEVERITY_ERROR


def test_parse_missing_id() -> None:
    result = parse_marker_line(f"{PREFIX} type=requirement")
    assert result.marker is not None
    assert result.marker.trace_id is None
    missing = [d for d in result.diagnostics if d.rule_id == "TL004" and "require id" in d.message]
    assert len(missing) == 1


def test_parse_marker_with_edges_but_no_id() -> None:
    result = parse_marker_line(f"{PREFIX} work=WORK-1")
    assert result.marker is not None
    assert result.marker.trace_id is None
    assert result.marker.edges == {"work": ["WORK-1"]}
    assert any(d.rule_id == "TL004" and "require id" in d.message for d in result.diagnostics)


@pytest.mark.parametrize("bad", ["REQ-1!", "a,b", "a#b", "a@b", "a+b"])
def test_parse_invalid_id(bad: str) -> None:
    result = parse_marker_line(f"{PREFIX} id={bad}")
    assert result.marker is not None
    assert result.marker.trace_id == bad
    tl005 = [d for d in result.diagnostics if d.rule_id == "TL005"]
    assert len(tl005) == 1
    assert tl005[0].severity == SEVERITY_ERROR


# ---------------------------------------------------------------------------
# Duplicate keys (TL006)
# ---------------------------------------------------------------------------


def test_parse_duplicate_key_first_wins() -> None:
    result = parse_marker_line(f"{PREFIX} id=A id=B")
    assert result.marker is not None
    assert result.marker.trace_id == "A"
    dup = [d for d in result.diagnostics if d.rule_id == "TL006"]
    assert len(dup) == 1
    assert dup[0].severity == SEVERITY_ERROR


def test_parse_duplicate_edge_key_skips_second() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x satisfies=REQ-1 satisfies=REQ-2")
    assert result.marker is not None
    assert result.marker.edges == {"satisfies": ["REQ-1"]}
    dup = [d for d in result.diagnostics if d.rule_id == "TL006"]
    assert len(dup) == 1


def test_parse_empty_value_diag() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 title=")
    assert result.marker is not None
    assert result.marker.title is None
    assert any(d.rule_id == "TL004" for d in result.diagnostics)


# ---------------------------------------------------------------------------
# Convenience keys and edge targets
# ---------------------------------------------------------------------------


def test_convenience_work_key() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x work=WORK-1")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.edges == {"work": ["WORK-1"]}


def test_convenience_plan_key_aliases_implements() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x plan=PLAN-1")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.edges == {"implements": ["PLAN-1"]}


def test_comma_separated_targets() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x satisfies=REQ-1,REQ-2 work=WORK-1,WORK-2")
    assert result.marker is not None
    assert result.diagnostics == []
    assert result.marker.edges == {
        "satisfies": ["REQ-1", "REQ-2"],
        "work": ["WORK-1", "WORK-2"],
    }


def test_quoted_target_is_single() -> None:
    result = parse_marker_line(f'{PREFIX} id=impl.x work="WORK-1"')
    assert result.marker is not None
    assert result.marker.edges == {"work": ["WORK-1"]}


def test_trailing_comma_target_filtered() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x satisfies=REQ-1,")
    assert result.marker is not None
    assert result.marker.edges == {"satisfies": ["REQ-1"]}


def test_invalid_edge_target_diag() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x satisfies=REQ-1,a@b")
    assert result.marker is not None
    assert result.marker.edges == {"satisfies": ["REQ-1", "a@b"]}
    tl004 = [d for d in result.diagnostics if d.rule_id == "TL004" and "edge target" in d.message]
    assert len(tl004) == 1


@pytest.mark.parametrize("key", sorted(SEMANTIC_EDGES - {"work"}))
def test_semantic_edges_declarable(key: str) -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x {key}=REQ-1")
    assert result.marker is not None
    assert result.marker.edges == {key: ["REQ-1"]}
    assert [d for d in result.diagnostics if d.severity == SEVERITY_ERROR] == []


# ---------------------------------------------------------------------------
# Derived keys rejected (TL040)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(STRUCTURAL_EDGES))
def test_structural_keys_rejected(key: str) -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x {key}=impl.y")
    assert result.marker is not None
    assert key not in result.marker.edges
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_ERROR
    assert "derived relationship" in tl040[0].message


@pytest.mark.parametrize("key", sorted(OBSERVED_EDGES))
def test_observed_keys_rejected(key: str) -> None:
    result = parse_marker_line(f"{PREFIX} id=test.t1 {key}=impl.x")
    assert result.marker is not None
    assert key not in result.marker.edges
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_ERROR
    assert "derived relationship" in tl040[0].message


def test_derived_keys_rejected_even_permissive() -> None:
    result = parse_marker_line(f"{PREFIX} id=impl.x calls=impl.y", unknown_keys="permissive")
    assert result.marker is not None
    assert result.migrated == {}
    assert result.marker.edges == {}
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_ERROR


# ---------------------------------------------------------------------------
# Unknown keys: error / warning / permissive / migration modes
# ---------------------------------------------------------------------------


def test_unknown_key_error_mode() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 foo=bar")
    assert result.marker is not None
    assert result.migrated == {}
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_ERROR


def test_unknown_key_warning_mode() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 foo=bar", unknown_keys="warning")
    assert result.marker is not None
    assert result.migrated == {}
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_WARNING


def test_unknown_key_permissive_mode() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 foo=bar", unknown_keys="permissive")
    assert result.marker is not None
    assert result.migrated == {"foo": "bar"}
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_INFO


def test_unknown_key_migration_mode() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1 foo=bar", unknown_keys="migration")
    assert result.marker is not None
    assert result.migrated == {"foo": "bar"}
    tl040 = [d for d in result.diagnostics if d.rule_id == "TL040"]
    assert len(tl040) == 1
    assert tl040[0].severity == SEVERITY_INFO


def test_unknown_quoted_key_permissive_preserves_value() -> None:
    result = parse_marker_line(f'{PREFIX} id=REQ-1 foo="bar baz"', unknown_keys="permissive")
    assert result.marker is not None
    assert result.migrated == {"foo": "bar baz"}


# ---------------------------------------------------------------------------
# Non-marker lines and hit iteration
# ---------------------------------------------------------------------------


def test_non_marker_line_empty_result() -> None:
    result = parse_marker_line("just a comment, no marker")
    assert result.marker is None
    assert result.diagnostics == []
    assert result.migrated == {}


def test_parse_marker_line_defaults() -> None:
    result = parse_marker_line(f"{PREFIX} id=REQ-1")
    assert result.marker is not None
    assert result.marker.path == "<string>"
    assert result.marker.line == 1


def test_iter_marker_hits() -> None:
    text = f"# {PREFIX} id=REQ-1\nplain line\n// {PREFIX} id=impl.x\n"
    hits = list(iter_marker_hits(text, path="src/a.py"))
    assert len(hits) == 2
    first, second = hits
    assert first.path == "src/a.py"
    assert first.line == 1
    assert first.column == 3
    assert first.raw == f"# {PREFIX} id=REQ-1"
    assert first.payload == f"{PREFIX} id=REQ-1"
    assert second.line == 3
    assert second.column == 4
    assert second.payload == f"{PREFIX} id=impl.x"


def test_iter_marker_hits_skips_non_comment_context() -> None:
    text = f"x = {PREFIX} id=REQ-1\ny = 1  # {PREFIX} id=REQ-2\n"
    assert list(iter_marker_hits(text, path="p")) == []


def test_parse_marker_hit_uses_hit_fields() -> None:
    raw = f"# {PREFIX} id=REQ-1"
    hit = MarkerHit(path="src/a.py", line=7, column=3, raw=raw, payload=f"{PREFIX} id=REQ-1")
    result = parse_marker_hit(hit)
    assert result.marker is not None
    assert result.marker.path == "src/a.py"
    assert result.marker.line == 7
    assert result.marker.column == 3
    assert result.marker.raw == raw
    assert result.marker.trace_id == "REQ-1"
    assert result.diagnostics == []


# ---------------------------------------------------------------------------
# render_marker
# ---------------------------------------------------------------------------


def test_render_marker_basic() -> None:
    marker = ParsedMarker(
        path="p",
        line=1,
        column=1,
        raw="",
        trace_id="REQ-1",
        node_type="requirement",
        title="My req",
        properties={"policy": "POL-1"},
        edges={"satisfies": ["REQ-2"]},
    )
    assert render_marker(marker) == (
        f'{PREFIX} id=REQ-1 type=requirement title="My req" policy=POL-1 satisfies=REQ-2'
    )


def test_render_marker_minimal() -> None:
    marker = ParsedMarker(path="p", line=1, column=1, raw="", trace_id="REQ-1")
    assert render_marker(marker) == f"{PREFIX} id=REQ-1"


def test_render_edge_order_follows_registry() -> None:
    marker = ParsedMarker(
        path="p",
        line=1,
        column=1,
        raw="",
        trace_id="impl.x",
        edges={"satisfies": ["REQ-9"], "implements": ["PLAN-1"], "work": ["WORK-1"]},
    )
    assert render_marker(marker) == (
        f"{PREFIX} id=impl.x work=WORK-1 satisfies=REQ-9 implements=PLAN-1"
    )


@pytest.mark.parametrize(
    "line",
    [
        f"{PREFIX} id=REQ-1 type=requirement title=Foo work=WORK-1",
        f"{PREFIX} id=impl.x verifies=REQ-1 exercises=impl.y",
        f'{PREFIX} id=test.t1 title="Quoted title" policy=POL-7',
        f"{PREFIX} id=PLAN-1/P3 plan=PLAN-1",
        f"{PREFIX} id=ADR-42 supersedes=ADR-21 addresses=REQ-AUTH-017",
        f'{PREFIX} id=impl.x title="esc \\"q\\" \\\\ back" satisfies=REQ-1',
    ],
)
def test_render_parse_round_trip_fields(line: str) -> None:
    first = parse_marker_line(line)
    assert first.marker is not None
    rendered = render_marker(first.marker)
    second = parse_marker_line(rendered)
    assert second.marker is not None
    for attr in ("trace_id", "node_type", "title", "properties", "edges"):
        assert getattr(second.marker, attr) == getattr(first.marker, attr)


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_node_defining_keys() -> None:
    assert BUILTIN_PROPERTIES == {
        "id",
        "type",
        "title",
        "policy",
        "state",
        "canonical_source",
        "value",
    }
    assert CONVENIENCE_EDGES == {"work": "work", "plan": "implements"}
    # Every built-in property and semantic edge key is accepted by the parser;
    # derived (structural/observed) keys are rejected (see test_derived_keys_rejected).
    assert SEMANTIC_EDGES
