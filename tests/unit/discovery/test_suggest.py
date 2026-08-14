"""MarkerSuggestionEngine golden tests (review: every role, exact output)."""

from __future__ import annotations

from tracelayer.discovery.suggest import suggest_marker


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest type=test
def _boundary(name, lang: str, start: int = 1, kind: str = "function"):
    from tracelayer.discovery.boundaries import Boundary

    return Boundary(
        name=name, kind=kind, start_line=start, end_line=start, source="x", language=lang
    )


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_impl_marker type=test
def test_impl_marker():
    b = _boundary("rotate_refresh_token", "python")
    s = suggest_marker(b, "src/auth.py", work="WORK-1", requirement="REQ-1", plan="PLAN-1")
    assert s.role == "impl"
    assert s.marker == (
        "# trace:v1 id=impl.rotate-refresh-token work=WORK-1 satisfies=REQ-1 implements=PLAN-1"
    )


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_test_marker_with_exercises type=test
def test_test_marker_with_exercises():
    b = _boundary("test_rotation", "python")
    s = suggest_marker(
        b, "tests/test_auth.py", work="WORK-1", requirement="REQ-1", exercised="impl.rotate"
    )
    assert s.role == "test"
    assert s.marker == (
        "# trace:v1 id=test.test-rotation work=WORK-1 verifies=REQ-1 exercises=impl.rotate"
    )
    assert "documents" not in s.marker  # the documents= bug is gone


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_doc_marker_gets_documents type=test
def test_doc_marker_gets_documents():
    b = _boundary("Refresh Token Rotation", "markdown", kind="heading")
    s = suggest_marker(b, "docs/ops.md", work="WORK-1", requirement="REQ-1")
    assert s.role == "doc"
    assert s.marker == (
        "<!-- trace:v1 id=doc.refresh-token-rotation work=WORK-1 documents=REQ-1 -->"
    )


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_ops_marker_yaml_syntax type=test
def test_ops_marker_yaml_syntax():
    b = _boundary("parakeet", "yaml", kind="config-key")
    s = suggest_marker(b, "docker-compose.yml", work="WORK-1", requirement="REQ-1")
    assert s.role == "ops"
    assert s.marker == "# trace:v1 id=ops.parakeet work=WORK-1 satisfies=REQ-1"


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_json_sidecar_suggestion type=test
def test_json_sidecar_suggestion():
    b = _boundary("server", "json", kind="config-key")
    s = suggest_marker(b, "config.json", requirement="REQ-1")
    assert s.role == "ops"
    assert s.sidecar == ".trace/sidecars/config.json.json"
    assert "sidecar" in s.note


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_ts_syntax type=test
def test_ts_syntax():
    b = _boundary("handleRequest", "typescript")
    s = suggest_marker(b, "src/routes.ts", work="WORK-1")
    assert s.marker.startswith("// trace:v1 id=impl.handle-request work=WORK-1")


# trace:v1 id=test.dogfood.tests.unit.discovery.test_suggest.test_slug_sanitizes_spaces_and_punctuation type=test
def test_slug_sanitizes_spaces_and_punctuation():
    b = _boundary("Retry strategy!", "markdown", kind="heading")
    s = suggest_marker(b, "docs/ops.md", requirement="REQ-1")
    assert "retry-strategy" in s.marker
