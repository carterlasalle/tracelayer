"""Unit tests for the \x74race:v1 marker grammar (spec 11.1-11.2).

Covers comment extraction (`#`, `//`, `--`, `%`, `;`, `*`, `/* */`, `<!-- -->`,
plain text), tokenizing, quoted/unquoted values, escapes, unterminated quotes,
and invalid unquoted characters.
"""

from __future__ import annotations

import pytest

from tracelayer.diagnostics import SEVERITY_ERROR
from tracelayer.protocol import grammar

PREFIX = grammar.PREFIX  # "\x74race:v1"


# ---------------------------------------------------------------------------
# Comment extraction
# ---------------------------------------------------------------------------


# trace:v1 id=test.dogfood.tests.unit.protocol.test_grammar.py type=test
def test_extract_plain_line() -> None:
    assert grammar.extract_marker_payload(f"{PREFIX} id=REQ-1") == f"{PREFIX} id=REQ-1"


@pytest.mark.parametrize(
    "line",
    [
        f"# {PREFIX} id=REQ-1",
        f"// {PREFIX} id=REQ-1",
        f"-- {PREFIX} id=REQ-1",
        f"% {PREFIX} id=REQ-1",
        f"; {PREFIX} id=REQ-1",
        f"* {PREFIX} id=REQ-1",
        f"/* {PREFIX} id=REQ-1",
        f"<!-- {PREFIX} id=REQ-1 -->",
    ],
)
def test_extract_comment_prefixes(line: str) -> None:
    assert grammar.extract_marker_payload(line) == f"{PREFIX} id=REQ-1"


def test_extract_leading_whitespace() -> None:
    assert grammar.extract_marker_payload(f"   # {PREFIX} id=REQ-1") == f"{PREFIX} id=REQ-1"
    assert grammar.extract_marker_payload(f"  {PREFIX} id=REQ-1") == f"{PREFIX} id=REQ-1"


def test_extract_block_comment_closers_stripped() -> None:
    assert grammar.extract_marker_payload(f"/* {PREFIX} id=REQ-1 */") == f"{PREFIX} id=REQ-1"
    assert grammar.extract_marker_payload(f"<!-- {PREFIX} id=REQ-1 -->") == f"{PREFIX} id=REQ-1"


def test_extract_comment_with_leading_text() -> None:
    assert grammar.extract_marker_payload(f"// note: {PREFIX} id=REQ-1") == f"{PREFIX} id=REQ-1"


def test_extract_marker_after_code_not_comment_start() -> None:
    # The marker must start the comment; trailing inline comments are ignored.
    assert grammar.extract_marker_payload(f"x = 1  # {PREFIX} id=REQ-1") is None
    assert grammar.extract_marker_payload(f"x#{PREFIX} id=REQ-1") is None


def test_extract_not_comment_context() -> None:
    assert grammar.extract_marker_payload(f"x = {PREFIX} id=REQ-1") is None
    assert grammar.extract_marker_payload(f"text {PREFIX} id=REQ-1") is None


def test_extract_prefix_word_boundary() -> None:
    assert grammar.extract_marker_payload(f"{PREFIX}x id=REQ-1") is None
    assert grammar.extract_marker_payload(f"{PREFIX}id=REQ-1") is None


def test_extract_blank_and_empty_lines() -> None:
    assert grammar.extract_marker_payload("") is None
    assert grammar.extract_marker_payload("   ") is None
    assert grammar.extract_marker_payload("no marker here") is None


def test_extract_bare_prefix() -> None:
    assert grammar.extract_marker_payload(f"# {PREFIX}") == PREFIX


def test_extract_payload_keeps_rest() -> None:
    assert grammar.extract_marker_payload(f"// {PREFIX} a=b c=d") == f"{PREFIX} a=b c=d"


# ---------------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------------


def tokenize(payload: str, path: str = "<test>", line: int = 1):
    return grammar.tokenize_fields(payload, path=path, line=line)


def test_tokenize_simple_fields() -> None:
    tokens, diags = tokenize(f"{PREFIX} id=REQ-1 type=requirement")
    assert diags == []
    assert [(t.key, t.value, t.quoted) for t in tokens] == [
        ("id", "REQ-1", False),
        ("type", "requirement", False),
    ]


def test_tokenize_quoted_value() -> None:
    tokens, diags = tokenize(f'{PREFIX} title="Hello World"')
    assert diags == []
    assert len(tokens) == 1
    assert tokens[0].key == "title"
    assert tokens[0].value == "Hello World"
    assert tokens[0].quoted is True


def test_tokenize_escapes() -> None:
    tokens, diags = tokenize(f'{PREFIX} title="a\\nb\\t\\"c\\\\d"')
    assert diags == []
    assert len(tokens) == 1
    assert tokens[0].value == 'a\nb\t"c\\d'
    assert tokens[0].quoted is True


# trace:v1 id=test.grammar.regex-passthrough type=test verifies=REQ-confined-live-fact-verification
def test_tokenize_regex_escapes_pass_through() -> None:
    tokens, diags = tokenize(f'{PREFIX} title="v (\\S+)"')
    assert diags == []
    assert tokens[0].value == "v (\\S+)"


def test_tokenize_unknown_escape() -> None:
    _tokens, diags = tokenize(f'{PREFIX} title="\\q"')
    unknown = [d for d in diags if "Unknown escape" in d.message]
    assert len(unknown) == 1
    assert unknown[0].rule_id == "TL004"
    assert unknown[0].severity == SEVERITY_ERROR


def test_tokenize_unterminated_escape() -> None:
    _tokens, diags = tokenize(f'{PREFIX} title="abc\\')
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Unterminated escape" in diags[0].message


def test_tokenize_unterminated_quoted() -> None:
    _tokens, diags = tokenize(f'{PREFIX} title="abc')
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Unterminated quoted value" in diags[0].message


def test_tokenize_missing_equals() -> None:
    _tokens, diags = tokenize(f"{PREFIX} id REQ-1")
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Missing '='" in diags[0].message


def test_tokenize_expected_key_value() -> None:
    _tokens, diags = tokenize(f"{PREFIX} =x")
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Expected key=value" in diags[0].message


def test_tokenize_digit_starting_key() -> None:
    _tokens, diags = tokenize(f"{PREFIX} 1key=v")
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Expected key=value" in diags[0].message


def test_tokenize_invalid_unquoted_chars() -> None:
    tokens, diags = tokenize(f"{PREFIX} id=REQ-1 title=bad$value")
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert "Invalid characters in unquoted value" in diags[0].message
    # The token is still emitted with the raw value.
    assert tokens[-1].key == "title"
    assert tokens[-1].value == "bad$value"
    assert tokens[-1].quoted is False


def test_tokenize_valid_unquoted_char_set() -> None:
    value = "A1._:/#@,+-"
    tokens, diags = tokenize(f"{PREFIX} id={value}")
    assert diags == []
    assert tokens[0].value == value


def test_tokenize_empty_value() -> None:
    tokens, diags = tokenize(f"{PREFIX} id=")
    assert len(diags) == 1
    assert diags[0].rule_id == "TL004"
    assert tokens[0].value == ""


def test_tokenize_bare_prefix() -> None:
    tokens, diags = tokenize(PREFIX)
    assert tokens == []
    assert diags == []


def test_tokenize_tabs_and_spaces_separators() -> None:
    tokens, diags = tokenize(f"{PREFIX}\tid=REQ-1\t\ttype=requirement  title=x")
    assert diags == []
    assert [t.key for t in tokens] == ["id", "type", "title"]


def test_tokenize_key_with_underscore() -> None:
    tokens, diags = tokenize(f"{PREFIX} my_key=v")
    assert diags == []
    assert tokens[0].key == "my_key"


def test_tokenize_offsets() -> None:
    tokens, diags = tokenize(f"{PREFIX} id=REQ-1 title=x")
    assert diags == []
    assert [(t.key, t.value) for t in tokens] == [("id", "REQ-1"), ("title", "x")]


def test_tokenize_quoted_token_value() -> None:
    tokens, diags = tokenize(f'{PREFIX} title="abc"')
    assert diags == []
    assert tokens[0].key == "title"
    assert tokens[0].value == "abc"
    assert tokens[0].quoted is True


# ---------------------------------------------------------------------------
# quote_value
# ---------------------------------------------------------------------------


def test_quote_value_unquoted() -> None:
    for value in ("REQ-1", "impl.auth.refresh", "A1._:/#@,+-", "a/b", "a-b_c"):
        assert grammar.quote_value(value) == value


def test_quote_value_quoted() -> None:
    assert grammar.quote_value("Hello World") == '"Hello World"'
    assert grammar.quote_value('a"b') == '"a\\"b"'
    assert grammar.quote_value("a\\b") == '"a\\\\b"'
    assert grammar.quote_value("a\nb") == '"a\\nb"'
    assert grammar.quote_value("a\tb") == '"a\\tb"'


def test_quote_value_empty() -> None:
    assert grammar.quote_value("") == '""'
