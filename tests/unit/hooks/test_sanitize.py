"""Tests for tracelayer.hooks.common sanitization (T1: bounded repository text)."""

from __future__ import annotations

import re

from tracelayer.hooks.common import fit, sanitize_text

_PREFIX = "repository data: "
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# trace:v1 id=test.dogfood.tests.unit.hooks.test_sanitize.py type=test
def test_sanitize_collapses_whitespace():
    assert sanitize_text("  a\n\t b   ") == _PREFIX + "a b"
    assert sanitize_text("multi\nline\n text") == _PREFIX + "multi line text"


def test_sanitize_strips_control_chars():
    out = sanitize_text("a\x00b\x1b[31mc\x7fd")
    assert _CONTROL.search(out) is None
    assert out == _PREFIX + "ab[31mcd"


def test_sanitize_bounds_length():
    long_text = "x" * 500
    out = sanitize_text(long_text, max_chars=50)
    assert out.startswith(_PREFIX)
    data = out[len(_PREFIX) :]
    assert len(data) == 50
    assert data.endswith("\u2026")
    assert _CONTROL.search(out) is None


def test_sanitize_short_text_unchanged():
    assert sanitize_text("hello", max_chars=200) == _PREFIX + "hello"


def test_sanitize_empty_string():
    assert sanitize_text("") == _PREFIX


def test_sanitize_prefix_marks_repository_data():
    out = sanitize_text("anything at all")
    assert out.startswith(_PREFIX)
    assert out == _PREFIX + "anything at all"


def test_fit_hard_bound():
    assert fit("x" * 100, 50) == "x" * 49 + "\u2026"
    assert len(fit("x" * 100, 50)) == 50


def test_fit_short_text_unchanged():
    assert fit("abc", 10) == "abc"


def test_fit_zero_or_negative_cap():
    assert fit("abc", 0) == ""
    assert fit("abc", -5) == ""
    assert fit("", 10) == ""
