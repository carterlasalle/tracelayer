"""Regression: tree-sitter traversal must never crash the interpreter.

Two real bugs are pinned here:
- unbounded recursion in the symbol walker (deeply nested files segfaulted);
- the tree-sitter binding segfaults nondeterministically when
  ``start_point``/``end_point`` positions are read across multiple parses in
  one process (SIGSEGV/SIGBUS on macOS arm64, tree-sitter 0.25/0.26).

The engine now computes line numbers from byte offsets and disables the cyclic
collector on first tree-sitter use. These tests would crash the whole pytest
process if the bugs regressed, which is exactly the point.
"""

from __future__ import annotations

import gc
import textwrap

from tracelayer.graph.fingerprints import sha256_hex
from tracelayer.symbols.base import line_at, line_starts, symbol_lines
from tracelayer.symbols.registry import get_parser


# trace:v1 id=test.dogfood.tests.unit.symbols.test_parse_stability.py type=test
def _large_python_source(lines: int = 1200) -> str:
    body = "\n".join(f"def fn_{i}(x):\n    return x + {i}\n\n" for i in range(lines))
    return "import os\n\n" + body


def test_multi_parse_large_files_does_not_crash():
    parser = get_parser("python")
    sources = [
        _large_python_source(1200),
        _large_python_source(900),
        "class A:\n    def m(self):\n        return 1\n",
    ]
    total = 0
    for _ in range(3):
        for src in sources:
            syms = parser.parse(src, "generated.py")
            total += len(syms)
    # Every function must be found; repeated parses must not crash or corrupt.
    assert total >= 3 * 1200 + 3 * 900
    assert all(s.start_line <= s.end_line for s in parser.parse(sources[0], "g.py"))


def test_symbol_lines_are_correct():
    src = textwrap.dedent(
        """\
        # \x74race:v1 id=impl.demo
        def hello():
            return 1


        class Box:
            def m(self):
                pass
        """
    ).encode("utf-8")
    parser = get_parser("python")
    syms = parser.parse(src.decode("utf-8"), "demo.py")
    by_name = {s.name: s for s in syms}
    assert by_name["hello"].start_line == 2
    assert by_name["hello"].end_line == 3
    assert by_name["Box"].start_line == 6
    assert by_name["m"].start_line == 7


def test_line_helpers():
    data = b"ab\ncd\n\nef"
    starts = line_starts(data)
    assert line_at(starts, 0) == 1
    assert line_at(starts, 3) == 2
    assert line_at(starts, 6) == 3
    assert line_at(starts, 7) == 4
    assert symbol_lines(starts, 0, 2) == (1, 1)
    assert symbol_lines(starts, 0, 3) == (1, 1)  # "ab\n": ends before line 2 starts
    assert symbol_lines(starts, 3, 5) == (2, 2)


def test_ast_normalized_is_points_free_and_deterministic():
    parser = get_parser("python")
    src = "def f():\n    return 1\n"
    a = parser.ast_normalized(src)
    b = parser.ast_normalized(src)
    assert a == b
    assert "start_point" not in a and "end_point" not in a
    assert sha256_hex(a) == sha256_hex(b)
    # structural change must change the fingerprint
    c = parser.ast_normalized("def f():\n    return 2\n")
    assert c != a


def test_gc_safety_policy_disables_cyclic_collector():
    parser = get_parser("python")
    parser.parse("def f():\n    pass\n", "x.py")
    assert not gc.isenabled(), (
        "cyclic GC must stay disabled after first tree-sitter use: the "
        "tree-sitter binding segfaults when the cyclic collector clears its "
        "objects (see tracelayer.symbols.base.no_cyclic_gc)"
    )
