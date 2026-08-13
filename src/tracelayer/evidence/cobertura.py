"""Cobertura XML coverage parser (FR-011, spec 25).

XML is untrusted input (spec 26.1): malformed content raises
CoberturaParseError (a ValueError); the ingest layer converts that into a
TL051 diagnostic. Parsing uses only the stdlib ``xml.etree.ElementTree``,
which does not expand external entities or DTDs, and input is size-capped
(MAX_EVIDENCE_BYTES) to bound the parsing DoS surface.  Parsing uses only the stdlib ``xml.etree.ElementTree``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 -- stdlib ET does not resolve

# external entities/DTDs; input is size-capped (MAX_EVIDENCE_BYTES) and
# malformed content becomes a TL051 diagnostic via CoberturaParseError.
from pathlib import Path

from tracelayer.evidence import MAX_EVIDENCE_BYTES


class CoberturaParseError(ValueError):
    """Raised when a Cobertura report cannot be parsed."""


def parse_cobertura(path: Path) -> dict[str, list[int]]:
    """Parse a Cobertura report into {file path: hit line numbers}.

    Walks ``<coverage>/<packages>/<classes>/<class filename=...>/<lines>``
    and keeps every ``<line number hits>`` with ``hits > 0``.  File paths
    are returned exactly as written in the report (relative to the coverage
    run), with hit lines sorted and deduplicated.  An empty coverage report
    yields ``{}``.
    """
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise CoberturaParseError(f"{path}: evidence file exceeds {MAX_EVIDENCE_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoberturaParseError(f"cannot read {path}: {exc}") from exc
    try:
        root = ET.fromstring(text)  # nosec B314
    except ET.ParseError as exc:
        raise CoberturaParseError(f"malformed XML in {path}: {exc}") from exc
    if root.tag != "coverage":
        raise CoberturaParseError(f"{path}: root element is <{root.tag}>, expected <coverage>")
    files: dict[str, list[int]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            raise CoberturaParseError(f"{path}: <class> without a filename attribute")
        hits: list[int] = []
        for line in cls.iter("line"):
            number = line.get("number")
            hit = line.get("hits")
            if number is None or hit is None:
                raise CoberturaParseError(f"{path}: <line> requires number and hits attributes")
            try:
                line_no = int(number)
                hit_count = int(hit)
            except ValueError as exc:
                raise CoberturaParseError(
                    f"{path}: bad line attributes number={number!r} hits={hit!r}"
                ) from exc
            if hit_count > 0:
                hits.append(line_no)
        files[filename] = sorted(set(hits))
    return files
