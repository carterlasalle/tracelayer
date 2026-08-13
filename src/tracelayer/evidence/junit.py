"""JUnit XML test report parser (FR-011, spec 25).

XML is untrusted input (spec 26.1): any malformed content raises
JUnitParseError (a ValueError); the ingest layer converts that into a TL051
diagnostic.  Parsing uses only the stdlib ``xml.etree.ElementTree``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 -- stdlib ET does not resolve

# external entities/DTDs; input is size-capped (MAX_EVIDENCE_BYTES) and
# malformed content becomes a TL051 diagnostic via JUnitParseError.
from pathlib import Path

from tracelayer.evidence import MAX_EVIDENCE_BYTES
from tracelayer.evidence.models import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_SKIP,
    TestOutcome,
)


class JUnitParseError(ValueError):
    """Raised when a JUnit report cannot be parsed."""


def parse_junit(path: Path) -> list[TestOutcome]:
    """Parse a JUnit XML report into TestOutcome records.

    Supports ``<testsuite>`` roots and nested ``<testsuites>`` containers;
    every ``<testcase>`` yields one outcome, decided by the first of
    ``<failure>`` (fail), ``<error>`` (error), ``<skipped>`` (skip), else
    pass.  The framework id is ``"<classname>.<name>"`` when a classname is
    present, matching the pytest dotted convention used by
    ``framework_id_of``; otherwise it is the bare test name.
    """
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise JUnitParseError(f"{path}: evidence file exceeds {MAX_EVIDENCE_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JUnitParseError(f"cannot read {path}: {exc}") from exc
    try:
        root = ET.fromstring(text)  # nosec B314
    except ET.ParseError as exc:
        raise JUnitParseError(f"malformed XML in {path}: {exc}") from exc
    if root.tag not in ("testsuite", "testsuites"):
        raise JUnitParseError(
            f"{path}: root element is <{root.tag}>, expected <testsuite> or <testsuites>"
        )
    outcomes: list[TestOutcome] = []
    for case in root.iter("testcase"):
        name = case.get("name")
        if not name:
            raise JUnitParseError(f"{path}: <testcase> without a name attribute")
        classname = case.get("classname") or ""
        framework_id = f"{classname}.{name}" if classname else name
        outcome = OUTCOME_PASS
        metadata: dict = {}
        for tag, result in (
            ("failure", OUTCOME_FAIL),
            ("error", OUTCOME_ERROR),
            ("skipped", OUTCOME_SKIP),
        ):
            child = case.find(tag)
            if child is not None:
                outcome = result
                if child.text and child.text.strip():
                    metadata[tag] = child.text.strip()
                break
        duration_ms = None
        time_attr = case.get("time")
        if time_attr is not None:
            try:
                duration_ms = float(time_attr) * 1000.0
            except ValueError:
                duration_ms = None  # optional attribute; a bad value is not fatal
        outcomes.append(
            TestOutcome(
                framework_id=framework_id,
                outcome=outcome,
                duration_ms=duration_ms,
                metadata=metadata,
            )
        )
    return outcomes
