"""JUnit / Cobertura evidence parsers (contract §E)."""

from __future__ import annotations

import pytest

from tracelayer.evidence.cobertura import CoberturaParseError, parse_cobertura
from tracelayer.evidence.junit import JUnitParseError, parse_junit


def write(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# JUnit
# --------------------------------------------------------------------------


def test_junit_pass_outcome(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_ok' classname='tests.app'/></testsuite>",
    )
    outcomes = parse_junit(path)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.framework_id == "tests.app.test_ok"
    assert o.outcome == "pass"
    assert o.duration_ms is None


def test_junit_fail_outcome_captures_message(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_ko' classname='tests.app'>"
        "<failure message='boom'>assert 1 == 2</failure></testcase></testsuite>",
    )
    outcomes = parse_junit(path)
    assert outcomes[0].outcome == "fail"
    assert outcomes[0].metadata["failure"] == "assert 1 == 2"


def test_junit_error_outcome(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_err' classname='tests.app'>"
        "<error message='exc'>TypeError</error></testcase></testsuite>",
    )
    assert parse_junit(path)[0].outcome == "error"


def test_junit_skip_outcome(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_skip' classname='tests.app'>"
        "<skipped/></testcase></testsuite>",
    )
    assert parse_junit(path)[0].outcome == "skip"


def test_junit_failure_takes_precedence_over_skip(tmp_path):
    """The first of failure/error/skipped decides the outcome."""
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_both' classname='tests.app'>"
        "<failure/><skipped/></testcase></testsuite>",
    )
    assert parse_junit(path)[0].outcome == "fail"


def test_junit_duration_ms_converted(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuite><testcase name='test_slow' classname='tests.app' time='1.25'/></testsuite>",
    )
    assert parse_junit(path)[0].duration_ms == 1250.0


def test_junit_nested_testsuites(tmp_path):
    path = write(
        tmp_path,
        "junit.xml",
        "<testsuites><testsuite><testcase name='a' classname='pkg.mod'/></testsuite>"
        "<testsuite><testcase name='b' classname='pkg.other'/></testsuite></testsuites>",
    )
    outcomes = parse_junit(path)
    assert [o.framework_id for o in outcomes] == ["pkg.mod.a", "pkg.other.b"]


def test_junit_bare_test_name_without_classname(tmp_path):
    path = write(tmp_path, "junit.xml", "<testsuite><testcase name='solo'/></testsuite>")
    assert parse_junit(path)[0].framework_id == "solo"


@pytest.mark.parametrize(
    "bad",
    [
        "<testsuite><testcase name='x'",  # truncated XML
        "not xml at all",
        "<testsuite><testcase/></testsuite>",  # missing name attribute
        "<report><testcase name='x'/></report>",  # wrong root element
    ],
)
def test_junit_malformed_raises(tmp_path, bad: str):
    path = write(tmp_path, "junit.xml", bad)
    with pytest.raises(JUnitParseError):
        parse_junit(path)


def test_junit_missing_file_raises(tmp_path):
    with pytest.raises(JUnitParseError):
        parse_junit(tmp_path / "does-not-exist.xml")


# --------------------------------------------------------------------------
# Cobertura
# --------------------------------------------------------------------------

COBERTURA_OK = """<?xml version="1.0"?>
<coverage line-rate="0.5">
  <packages>
    <package name="app">
      <classes>
        <class name="app.thing" filename="src/app.py">
          <lines>
            <line number="10" hits="1"/>
            <line number="11" hits="0"/>
            <line number="12" hits="7"/>
            <line number="13" hits="1"/>
          </lines>
        </class>
        <class name="app.other" filename="src/other.py">
          <lines>
            <line number="3" hits="2"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""


def test_cobertura_hit_lines_per_file(tmp_path):
    path = write(tmp_path, "coverage.xml", COBERTURA_OK)
    parsed = parse_cobertura(path)
    assert parsed == {"src/app.py": [10, 12, 13], "src/other.py": [3]}
    # zero-hit lines are dropped; hits are sorted and deduplicated


def test_cobertura_dedupes_and_sorts(tmp_path):
    path = write(
        tmp_path,
        "coverage.xml",
        "<coverage><packages><classes><class filename='f.py'><lines>"
        "<line number='5' hits='2'/><line number='5' hits='1'/>"
        "<line number='2' hits='1'/>"
        "</lines></class></classes></packages></coverage>",
    )
    assert parse_cobertura(path) == {"f.py": [2, 5]}


def test_cobertura_empty_report_yields_empty_dict(tmp_path):
    path = write(
        tmp_path,
        "coverage.xml",
        "<coverage><packages><classes></classes></packages></coverage>",
    )
    assert parse_cobertura(path) == {}


@pytest.mark.parametrize(
    "bad",
    [
        "<coverage><class/></coverage>",  # class without filename
        "<coverage><class filename='f.py'><lines>"
        "<line number='1'/></lines></class></coverage>",  # line without hits
        "<coverage><class filename='f.py'><lines>"
        "<line number='abc' hits='1'/></lines></class></coverage>",  # bad number
        "junk",  # not XML
        "<report/>",  # wrong root
    ],
)
def test_cobertura_malformed_raises(tmp_path, bad: str):
    path = write(tmp_path, "coverage.xml", bad)
    with pytest.raises(CoberturaParseError):
        parse_cobertura(path)


def test_cobertura_missing_file_raises(tmp_path):
    with pytest.raises(CoberturaParseError):
        parse_cobertura(tmp_path / "missing.xml")
