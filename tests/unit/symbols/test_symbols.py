"""Symbol parser and marker-attachment unit tests (spec 47.3 fixtures).

Parser coverage: python/js/ts/go/rust/java — function/method/class/nested/
decorated/multiline-signature cases via static fixture trees. Attachment:
determinism, gap rules (blank/comment/decorator), detached markers, and
ambiguity tie-breaking. ast_hash stability for unchanged source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracelayer.protocol import MarkerHit
from tracelayer.symbols.base import (
    SymbolRef,
    attach_markers,
    module_path,
)
from tracelayer.symbols.registry import get_parser, supported_languages

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "symbols"


# trace:v1 id=test.dogfood.tests.unit.symbols.test_symbols.py type=test
def _hits(lines: list[str], path: str = "f.py") -> list[MarkerHit]:
    return [
        MarkerHit(path=path, line=i + 1, column=1, raw=line, payload=line.split("\x74race:v1")[1])
        for i, line in enumerate(lines)
        if "\x74race:v1" in line
    ]


# ------------------------------------------------------------------ registry


def test_supported_languages() -> None:
    langs = supported_languages()
    assert langs == sorted(langs)
    assert langs == ["go", "java", "javascript", "python", "rust", "typescript"]


def test_get_parser_unknown_language() -> None:
    with pytest.raises(ValueError):
        get_parser("cobol")


def test_module_path() -> None:
    assert module_path("src/auth/tokens.py") == "src.auth.tokens"
    assert module_path("a.js") == "a"
    assert module_path("com/example/Foo.java") == "com.example.Foo"
    assert module_path("src\\win\\x.py") == "src.win.x"


# --------------------------------------------------- parser fixture coverage


def test_python_parser_fixture() -> None:
    src = (FIXTURES / "python" / "sample.py").read_text(encoding="utf-8")
    syms = get_parser("python").parse(src, "src/sample.py")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("function", "foo", "src.sample.foo"),
        ("class", "Bar", "src.sample.Bar"),
        ("method", "baz", "src.sample.Bar.baz"),
        ("method", "qux", "src.sample.Bar.qux"),
        ("function", "decorated", "src.sample.decorated"),
    ]


def test_python_decorated_definition_span() -> None:
    # The decorated_definition SymbolRef spans the decorator line(s), so the
    # start line is the decorator, not the def.
    src = "@deco\n@other\ndef foo():\n    pass\n"
    syms = get_parser("python").parse(src, "m.py")
    assert len(syms) == 1
    assert syms[0].kind == "function"
    assert syms[0].start_line == 1
    assert syms[0].end_line == 4
    assert (
        syms[0].source == "@deco\n@other\ndef foo():\n    pass"
    )  # node range excludes trailing newline


def test_python_nested_class_method_and_multiline_signature() -> None:
    src = """class Outer:
    class Inner:
        def method(self,
                   a: int,
                   b: str) -> None:
            pass
"""
    syms = get_parser("python").parse(src, "nested.py")
    got = [(s.kind, s.name, s.qualified_name, s.start_line) for s in syms]
    assert got == [
        ("class", "Outer", "nested.Outer", 1),
        ("class", "Inner", "nested.Outer.Inner", 2),
        ("method", "method", "nested.Outer.Inner.method", 3),
    ]
    # Multiline signature: the def line is the start, params span lines.
    assert syms[2].end_line == 6


def test_python_malformed_source_returns_symbols_so_far() -> None:
    # Malformed source must not raise; whatever parsed so far is returned.
    src = "def ok():\n    pass\n\ndef broken(:\n"
    syms = get_parser("python").parse(src, "m.py")
    assert syms and syms[0].name == "ok"


def test_javascript_parser_fixture() -> None:
    src = (FIXTURES / "javascript" / "sample.js").read_text(encoding="utf-8")
    syms = get_parser("javascript").parse(src, "src/ui.js")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("function", "greet", "src.ui.greet"),
        ("function", "arrow", "src.ui.arrow"),
        ("class", "Widget", "src.ui.Widget"),
        ("method", "render", "src.ui.Widget.render"),
        ("class", "Anon", "src.ui.Anon"),
    ]


def test_typescript_parser_fixture() -> None:
    src = (FIXTURES / "typescript" / "sample.ts").read_text(encoding="utf-8")
    syms = get_parser("typescript").parse(src, "src/model.ts")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("interface", "Shape", "src.model.Shape"),
        ("type_alias", "ID", "src.model.ID"),
        ("enum", "Color", "src.model.Color"),
        ("class", "Base", "src.model.Base"),
        ("function", "helper", "src.model.Util.helper"),
    ]


def test_go_parser_fixture() -> None:
    src = (FIXTURES / "go" / "sample.go").read_text(encoding="utf-8")
    syms = get_parser("go").parse(src, "src/main.go")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("function", "Add", "src.main.Add"),
        ("struct", "User", "src.main.User"),
        ("interface", "Speaker", "src.main.Speaker"),
        ("method", "FullName", "src.main.User.FullName"),
    ]


def test_rust_parser_fixture() -> None:
    src = (FIXTURES / "rust" / "sample.rs").read_text(encoding="utf-8")
    syms = get_parser("rust").parse(src, "src/geom.rs")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("struct", "Point", "src.geom.Point"),
        ("enum", "Shape", "src.geom.Shape"),
        ("trait", "Draw", "src.geom.Draw"),
        ("impl", "Point", "src.geom.Point"),
        ("method", "draw", "src.geom.Point.draw"),
        ("function", "free", "src.geom.free"),
    ]


def test_java_parser_fixture() -> None:
    src = (FIXTURES / "java" / "Greeter.java").read_text(encoding="utf-8")
    syms = get_parser("java").parse(src, "src/com/example/Greeter.java")
    got = [(s.kind, s.name, s.qualified_name) for s in syms]
    assert got == [
        ("class", "Greeter", "src.com.example.Greeter.Greeter"),
        ("constructor", "Greeter", "src.com.example.Greeter.Greeter.Greeter"),
        ("method", "greet", "src.com.example.Greeter.Greeter.greet"),
        ("interface", "Inner", "src.com.example.Greeter.Greeter.Inner"),
        ("method", "go", "src.com.example.Greeter.Greeter.Inner.go"),
        ("interface", "Service", "src.com.example.Greeter.Service"),
        ("method", "run", "src.com.example.Greeter.Service.run"),
        ("enum", "Mode", "src.com.example.Greeter.Mode"),
    ]


# ----------------------------------------------------- ast normalization


def test_ast_normalized_is_stable_for_unchanged_source() -> None:
    parser = get_parser("python")
    src = "def foo():\n    return 1\n"
    assert parser.ast_normalized(src) == parser.ast_normalized(src)


def test_ast_hash_stability_for_unchanged_source() -> None:
    parser = get_parser("python")
    src = "def foo(a):\n    return a\n"
    h1 = parser.parse(src, "m.py")[0].ast_hash()
    h2 = parser.parse(src, "m.py")[0].ast_hash()
    assert h1 == h2
    # A structural change (extra comment node inside the body) changes the
    # AST-normalized hash.
    h3 = parser.parse("def foo(a):\n    return a  # comment\n", "m.py")[0].ast_hash()
    assert h1 != h3
    # Whitespace-only formatting keeps the same AST -> same hash.
    h4 = parser.parse("def foo(a):\n    return a\n\n", "m.py")[0].ast_hash()
    assert h1 == h4


def test_symbol_ref_source_is_exact_bytes() -> None:
    src = "def foo():\n    pass\n"
    sym = get_parser("python").parse(src, "m.py")[0]
    assert sym.source == "def foo():\n    pass"  # node range, no trailing newline


# --------------------------------------------------------- attach_markers


def _sym(start: int, end: int, name: str = "fn", qname: str | None = None) -> SymbolRef:
    return SymbolRef(
        language="python",
        kind="function",
        name=name,
        qualified_name=qname or f"m.{name}",
        start_line=start,
        end_line=end,
        source="def x(): pass",
    )


def test_attach_marker_to_following_function() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "", "def foo():", "    pass"]
    hits = _hits(lines)
    attach = attach_markers([_sym(3, 4, "foo")], hits, lines)[0]
    assert attach.attachment_kind == "symbol"
    assert attach.symbol is not None and attach.symbol.qualified_name == "m.foo"
    assert attach.ambiguity is False


def test_attach_marker_through_comment_lines() -> None:
    lines = [
        "# \x74race:v1 id=REQ-1 type=requirement",
        "# docs",
        "// extra",
        "def foo():",
        "    pass",
    ]
    attach = attach_markers([_sym(4, 5, "foo")], _hits(lines), lines)[0]
    assert attach.attachment_kind == "symbol"
    assert attach.symbol is not None


def test_attach_marker_above_decorator() -> None:
    # The decorated definition starts at the decorator line, so a marker on
    # the line above attaches with no gap.
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "@deco", "def foo():", "    pass"]
    sym = SymbolRef(
        language="python",
        kind="function",
        name="foo",
        qualified_name="m.foo",
        start_line=2,
        end_line=3,
        source="@deco\ndef foo():\n    pass",
    )
    attach = attach_markers([sym], _hits(lines), lines)[0]
    assert attach.attachment_kind == "symbol"
    assert attach.symbol is not None and attach.symbol.start_line == 2


def test_attach_marker_gap_of_three_blank_lines_ok() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "", "", "", "def foo():", "    pass"]
    attach = attach_markers([_sym(5, 6, "foo")], _hits(lines), lines)[0]
    assert attach.attachment_kind == "symbol"


def test_attach_marker_detached_with_four_blank_lines() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "", "", "", "", "def foo():", "    pass"]
    attach = attach_markers([_sym(6, 7, "foo")], _hits(lines), lines)[0]
    assert attach.attachment_kind == "file"
    assert attach.symbol is None
    assert attach.ambiguity is False


def test_attach_marker_detached_with_code_in_gap() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "x = 1", "def foo():", "    pass"]
    attach = attach_markers([_sym(3, 4, "foo")], _hits(lines), lines)[0]
    assert attach.attachment_kind == "file"
    assert attach.symbol is None


def test_attach_marker_no_symbol_at_all() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "print('nothing here')"]
    attach = attach_markers([], _hits(lines), lines)[0]
    assert attach.attachment_kind == "file"
    assert attach.symbol is None


def test_attach_marker_ambiguous_tie_break() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "", "def z(): pass", "def a(): pass"]
    syms = [_sym(3, 3, "z", "m.z"), _sym(3, 3, "a", "m.a")]
    attach = attach_markers(syms, _hits(lines), lines)[0]
    assert attach.ambiguity is True
    # Deterministic tie-break: (start_line, qualified_name).
    assert attach.symbol is not None and attach.symbol.qualified_name == "m.a"


def test_attach_markers_deterministic_regardless_of_input_order() -> None:
    lines = ["# \x74race:v1 id=REQ-1 type=requirement", "", "def foo():", "    pass"]
    hits = _hits(lines)
    syms = [_sym(3, 4, "foo")]
    first = attach_markers(syms, hits, lines)
    second = attach_markers(list(reversed(syms)), list(reversed(hits)), lines)
    assert first == second
    attach = first[0]
    assert attach.symbol is not None
    assert attach.symbol.qualified_name == "m.foo"


def test_attach_markers_multiple_hits_order_preserved() -> None:
    lines = [
        "# \x74race:v1 id=REQ-1 type=requirement",
        "",
        "def foo():",
        "    pass",
        "",
        "# \x74race:v1 id=REQ-2 type=requirement",
        "def bar():",
        "    pass",
    ]
    attach = attach_markers([_sym(3, 4, "foo"), _sym(7, 8, "bar")], _hits(lines), lines)
    assert [a.symbol.qualified_name for a in attach] == ["m.foo", "m.bar"]
    assert all(a.attachment_kind == "symbol" for a in attach)


def test_attach_marker_realistic_python_file() -> None:
    # End-to-end: a marker line above a function in a real snippet attaches to
    # the function; a marker with a docstring target is fine (docstring is
    # inside the symbol, not in the gap).
    src = """# \x74race:v1 id=IMPL-1 type=implementation work=REQ-1
def compute(x):
    \"\"\"Compute x squared.\"\"\"
    return x * x
"""
    lines = [str(line) for line in src.splitlines()]
    sym = get_parser("python").parse(src, "compute.py")[0]
    attach = attach_markers([sym], _hits(lines), lines)[0]
    assert attach.attachment_kind == "symbol"
    assert attach.symbol.qualified_name == "compute.compute"
