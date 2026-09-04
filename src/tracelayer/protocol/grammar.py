"""trace:v1 marker grammar: comment extraction, tokenizing, escaping (spec 11.1-11.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tracelayer.diagnostics import Diagnostic, make

PREFIX = "trace:v1"
MARKER_START_RE = re.compile(r"trace:v1(?=\s|$)")
UNQUOTED_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/#@,+\-]+$")
_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# Comment openers accepted before the marker on one line, in precedence order.
_COMMENT_PREFIXES = ("<!--", "/*", "//", "#", "--", "%", ";", "*")

_ESCAPE_MAP = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}
# Regex class escapes pass through literally so selector patterns like
# ``regex:Version (\S+)`` survive quoted marker values. Every member
# previously errored, so no valid document changes meaning.
_REGEX_PASSTHROUGH = frozenset("dDsSwWbBAZ")


def extract_marker_payload(line: str) -> str | None:
    """Return the substring starting at `trace:v1` when the line carries a marker
    in a plausible comment context, else None.

    Accepts: `#`, `//`, `--`, `%`, `;`, `/* ... */` interiors, `<!-- ... -->`
    interiors, `*`-prefixed block-comment continuation lines, and plain text
    lines that begin with the prefix.
    """
    stripped = line.strip()
    if not stripped:
        return None
    m = MARKER_START_RE.search(stripped)
    if m is None:
        return None
    before = stripped[: m.start()]
    if not before:
        return stripped[m.start() :]
    if not any(before.startswith(p) for p in _COMMENT_PREFIXES):
        return None
    payload = stripped[m.start() :]
    # Strip block-comment closers that share the line. Values containing `-->`
    # or `*/` inside an HTML/block comment cannot be represented (documented).
    payload = payload.split("-->", 1)[0].split("*/", 1)[0].rstrip()
    return payload


@dataclass(frozen=True)
class FieldToken:
    key: str
    value: str  # unescaped
    quoted: bool


def tokenize_fields(
    payload: str, *, path: str, line: int
) -> tuple[list[FieldToken], list[Diagnostic]]:
    """Tokenize the fields after `trace:v1` into key/value pairs.

    Grammar: `key=value` pairs separated by whitespace. Unquoted values may
    contain `[A-Za-z0-9._:/#@,+-]`; values with whitespace or other characters
    MUST be double-quoted. Escapes inside quoted values: `\\`, `\"`, `\\n`, `\\t`.
    """
    diags: list[Diagnostic] = []
    tokens: list[FieldToken] = []
    rest = payload[len(PREFIX) :].lstrip()
    pos = 0
    n = len(rest)
    while pos < n:
        km = _KEY_RE.match(rest, pos)
        if not km:
            diags.append(
                make(
                    "TL004",
                    path=path,
                    line=line,
                    message=f"Expected key=value, found {rest[pos : pos + 20]!r}",
                )
            )
            break
        key = km.group(0)
        pos = km.end()
        while pos < n and rest[pos] == " ":
            pos += 1
        if pos >= n or rest[pos] != "=":
            diags.append(
                make("TL004", path=path, line=line, message=f"Missing '=' after key {key!r}")
            )
            break
        pos += 1
        while pos < n and rest[pos] == " ":
            pos += 1
        if pos < n and rest[pos] == '"':
            value, pos, err = _parse_quoted(rest, pos, path, line)
            if err is not None:
                diags.append(err)
            tokens.append(FieldToken(key, value, True))
        else:
            start = pos
            while pos < n and rest[pos] not in " \t":
                pos += 1
            value = rest[start:pos]
            if not UNQUOTED_VALUE_RE.match(value):
                diags.append(
                    make(
                        "TL004",
                        path=path,
                        line=line,
                        message=(
                            f"Invalid characters in unquoted value {value!r}; "
                            "quote the value or use only [A-Za-z0-9._:/#@,+-]"
                        ),
                    )
                )
            tokens.append(FieldToken(key, value, False))
        while pos < n and rest[pos] in " \t":
            pos += 1
    return tokens, diags


# trace:v1 id=impl.protocol.regex-passthrough work=WORK-close-adversarial-audit-gaps-on-knowledge-and-facts satisfies=REQ-confined-live-fact-verification
def _parse_quoted(s: str, pos: int, path: str, line: int) -> tuple[str, int, Diagnostic | None]:
    """Parse a double-quoted value starting at s[pos] == '"'.

    Returns (value, index after closing quote, error-or-None).
    """
    out: list[str] = []
    i = pos + 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 >= n:
                return (
                    "".join(out),
                    i + 1,
                    make("TL004", path=path, line=line, message="Unterminated escape sequence"),
                )
            e = s[i + 1]
            if e not in _ESCAPE_MAP:
                if e in _REGEX_PASSTHROUGH:
                    out.append("\\" + e)
                    i += 2
                    continue
                return (
                    "".join(out),
                    i + 2,
                    make("TL004", path=path, line=line, message=f"Unknown escape \\{e}"),
                )
            out.append(_ESCAPE_MAP[e])
            i += 2
            continue
        if c == '"':
            return "".join(out), i + 1, None
        out.append(c)
        i += 1
    return "".join(out), i, make("TL004", path=path, line=line, message="Unterminated quoted value")


def quote_value(value: str) -> str:
    """Render a value in canonical form: unquoted when possible, else quoted."""
    if value and UNQUOTED_VALUE_RE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'
