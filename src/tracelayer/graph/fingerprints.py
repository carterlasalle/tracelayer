"""Fingerprints for staleness detection (spec §G, Section 19).

Semantic fingerprints normalize a text block (LF endings, trailing
whitespace stripped per line, leading/trailing blank lines removed) before
hashing, so cosmetic edits do not invalidate traceability. Source
fingerprints hash the raw bytes for exact-change detection.
"""

from __future__ import annotations

import hashlib


def sha256_hex(data: str | bytes) -> str:
    """Lowercase hex sha256 digest of bytes or UTF-8-encoded text."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def normalize_block(text: str) -> str:
    """Normalize a text block: LF endings, per-line trailing whitespace
    stripped, leading and trailing blank lines removed."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def semantic_fingerprint(text: str) -> str:
    """sha256 of the normalized block (whitespace-insensitive)."""
    return sha256_hex(normalize_block(text))


def source_fingerprint(text: str) -> str:
    """sha256 of the raw text bytes (exact source hash)."""
    return sha256_hex(text)


def fingerprint_short(fp: str, n: int = 8) -> str:
    """Truncated fingerprint for display; ``n`` must be >= 0."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    return fp[:n]
