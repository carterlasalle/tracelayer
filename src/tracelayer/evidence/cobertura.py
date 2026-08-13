"""Cobertura XML coverage parser (FR-011, spec 25).

XML is untrusted input (spec 26.1): malformed content raises
CoberturaParseError (a ValueError); the ingest layer converts that into a
TL051 diagnostic.  Parsing uses only the stdlib ``xml.etree.ElementTree``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


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
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoberturaParseError(f"cannot read {path}: {exc}") from exc
    try:
        root = ET.fromstring(text)
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
