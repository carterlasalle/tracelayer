"""Fingerprint unit tests: sha256, block normalization, semantic vs source
fingerprints, and short-form truncation."""

from __future__ import annotations

import hashlib

import pytest

from tracelayer.graph.fingerprints import (
    fingerprint_short,
    normalize_block,
    semantic_fingerprint,
    sha256_hex,
    source_fingerprint,
)


def test_sha256_hex_known_digest() -> None:
    assert sha256_hex("abc") == hashlib.sha256(b"abc").hexdigest()
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert sha256_hex("") == hashlib.sha256(b"").hexdigest()
    assert sha256_hex("abc") == sha256_hex("abc")  # stable
    assert sha256_hex("abc") != sha256_hex("abd")


def test_normalize_block_line_endings_and_whitespace() -> None:
    assert normalize_block("a\r\nb\r\n") == "a\nb"
    assert normalize_block("a\rb\r") == "a\nb"
    assert normalize_block("  a  \n b \n") == "  a\n b"
    # Leading and trailing blank lines are stripped.
    assert normalize_block("\n\nhello\n\n") == "hello"
    assert normalize_block("") == ""
    # Interior blank lines survive.
    assert normalize_block("a\n\nb") == "a\n\nb"


def test_semantic_fingerprint_ignores_cosmetic_changes() -> None:
    base = "## Title\n\nSome body text.\n"
    assert semantic_fingerprint(base) == semantic_fingerprint(base.replace("\n", "\r\n"))
    # Trailing whitespace per line and trailing blank lines are normalized.
    assert semantic_fingerprint(base) == semantic_fingerprint("## Title  \n\nSome body text.\n\n")
    assert semantic_fingerprint("") == semantic_fingerprint("\n\n\n")
    # Real content changes alter the semantic fingerprint.
    assert semantic_fingerprint(base) != semantic_fingerprint(base + "extra\n")


def test_source_fingerprint_is_exact() -> None:
    src = "def foo():\n    return 1\n"
    assert source_fingerprint(src) == hashlib.sha256(src.encode("utf-8")).hexdigest()
    # Any byte change (even cosmetic) changes the source fingerprint.
    assert source_fingerprint(src) != source_fingerprint(src + "\n")
    assert source_fingerprint("a") != source_fingerprint("A")


def test_semantic_and_source_diverge_on_whitespace() -> None:
    a, b = "x = 1", "x = 1\n"
    assert semantic_fingerprint(a) == semantic_fingerprint(b)
    assert source_fingerprint(a) != source_fingerprint(b)


def test_fingerprint_short() -> None:
    fp = semantic_fingerprint("hello world")
    assert len(fp) == 64
    assert fingerprint_short(fp) == fp[:8]
    assert fingerprint_short(fp, 12) == fp[:12]
    assert fingerprint_short(fp, 0) == ""
    with pytest.raises(ValueError):
        fingerprint_short(fp, -1)
