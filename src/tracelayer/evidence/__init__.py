"""Runtime evidence ingestion (FR-011, spec 25)."""

from __future__ import annotations

# Evidence files are untrusted input (spec 26.1); cap their size to bound
# the XML-parsing DoS surface (spec T9). Parsers raise their parse error
# above this instead of parsing.
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
