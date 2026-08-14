"""Shared fixture content and setup helpers for integration tests.

Owned by the TestsE2E agent: everything here lives under tests/integration/
and is used by the CLI, hooks, and acceptance-matrix tests.  All repos are
built with the shared ``make_git_repo`` helper (fixed git identity), so
commit hashes and graph UIDs are deterministic per fixture content.

The AUTH fixture mirrors spec Section 50 (authentication feature): a
requirement, an ADR, a work item (with external mirrors), a plan, an
implementation, and a linked test.  The STRICT fixture is a minimal
requirement -> implementation -> test chain under the strict policy
profile (used for TL012/TL022/TL030 scenarios).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import make_git_repo, run_trace

# ---------------------------------------------------------------------------
# Auth feature fixture (spec 50)
# ---------------------------------------------------------------------------

AUTH_FILES: dict[str, str] = {
    "docs/reqs.md": """# Requirements

## REQ-AUTH-017 - Refresh token rotation

<!-- trace:v1 id=REQ-AUTH-017 type=requirement derived_from=PRD-AUTH-002 -->

Whenever a refresh token is exchanged, the previous token must become unusable.
""",
    "docs/prd.md": """# PRD-AUTH-002 - Authentication product requirements

<!-- trace:v1 id=PRD-AUTH-002 type=prd -->

Authentication foundations for v1.
""",
    "docs/adr.md": """# ADR-0042 - One-time refresh-token families

<!-- trace:v1 id=ADR-0042 type=decision addresses=REQ-AUTH-017 -->

Tokens are organized into families. Rotation revokes the previous token.
""",
    "docs/plan.md": """# Phase 3 - Rotation persistence

<!-- trace:v1 id=PLAN-AUTH-237/P3 type=plan work=WORK-AUTH-237 implements=ADR-0042 -->

Persist token-family rotation and rejection of reuse.
""",
    ".trace/work.toml": """[work."WORK-AUTH-237"]
title = "Implement refresh token rotation"

[work."WORK-AUTH-237".mirrors]
github_issue = "812"
jira = "AUTH-237"
""",
    "src/auth/tokens.py": '''"""Token helpers."""


# trace:v1 id=impl.auth.refresh work=WORK-AUTH-237 satisfies=REQ-AUTH-017 implements=ADR-0042
def rotate_refresh_token(token: str) -> str:
    """Rotate a refresh token."""
    return "rotated-" + token
''',
    "tests/test_auth.py": '''"""Auth tests."""


# trace:v1 id=test.auth.refresh-reuse verifies=REQ-AUTH-017 exercises=impl.auth.refresh
def test_reused_refresh_token_is_rejected():
    assert rotate("x") == "rotated-x"
''',
}

# The implementation symbol in src/auth/tokens.py spans lines 5-7 (1-based):
#  1  """Token helpers."""
#  2  (blank)
#  3  (blank)
#  4  # trace:v1 ... (marker)
#  5  def rotate_refresh_token(...):
#  6      """Rotate a refresh token."""
#  7      return "rotated-" + token
IMPL_LINES = (5, 7)
# The strict fixture's impl symbol in src/api.py spans lines 5-6.
STRICT_IMPL_LINES = (5, 6)
# A line inside the symbol body, used for pre-mutation payloads.
IMPL_BODY_LINE = 6

# JUnit report whose testcase name matches the framework id of
# test.auth.refresh-reuse (pytest dotted convention).
JUNIT_PASS = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="auth" tests="1" failures="0" errors="0" skipped="0">
  <testcase name="tests.test_auth.test_reused_refresh_token_is_rejected" time="0.01"/>
</testsuite>
"""


def cobertura_for(lines: tuple[int, int], filename: str = "src/auth/tokens.py") -> str:
    """Cobertura report covering exactly the given line range of a file."""
    hits = "".join(
        f'            <line number="{n}" hits="1"/>\n' for n in range(lines[0], lines[1] + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<coverage line-rate="1.0">
  <packages>
    <package name="src.auth">
      <classes>
        <class name="tokens" filename="{filename}">
          <lines>
{hits}          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def setup_auth_repo(tmp_path: Path) -> Path:
    """make_git_repo(AUTH_FILES) + ``trace init`` (config only) + full index."""
    root = make_git_repo(tmp_path, dict(AUTH_FILES))
    _expect_ok(run_trace(root, "init", "--no-skill", "--no-mcp"))
    _expect_ok(run_trace(root, "index", "--all"))
    return root


def head_revision(root: Path) -> str:
    """Current HEAD sha of the fixture repo."""
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def ingest_pass_evidence(root: Path, tmp_path: Path, revision: str) -> None:
    """Ingest a passing JUnit report plus suite coverage of the impl symbol."""
    junit = tmp_path / "junit.xml"
    cobertura = tmp_path / "cobertura.xml"
    junit.write_text(JUNIT_PASS, encoding="utf-8")
    cobertura.write_text(cobertura_for(IMPL_LINES), encoding="utf-8")
    proc = run_trace(
        root,
        "evidence",
        "ingest",
        "--junit",
        str(junit),
        "--coverage",
        str(cobertura),
        "--revision",
        revision,
        "--provider",
        "pytest",
        "--workflow",
        "ci",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def change_requirement(root: Path) -> None:
    """Edit the REQ-AUTH-017 body in the working tree (no commit)."""
    path = root / "docs" / "reqs.md"
    text = path.read_text(encoding="utf-8")
    assert "must become unusable" in text
    path.write_text(
        text.replace(
            "must become unusable",
            "must become unusable and its successor recorded",
        ),
        encoding="utf-8",
    )


def strict_files() -> dict[str, str]:
    """Minimal traced chain under the strict profile (committed config)."""
    return {
        ".gitignore": ".trace/cache/\n",
        ".trace/trace.toml": """schema_version = 1
repo_id = "strict-fixture"
cache_dir = ".trace/cache"

[index]
respect_gitignore = true
incremental = true
fts = true

[index.languages]
python = true
typescript = true
javascript = true
go = true
rust = true
java = true
cpp = false

[markers]
prefix = "trace:v1"
unknown_keys = "error"
""",
        ".trace/policy.toml": """profile = "strict"

[lifecycle]
default = "wip"
ci = "merge"
""",
        ".trace/work.toml": '[work."WORK-STR-1"]\ntitle = "Strict endpoint work"\n',
        "docs/req.md": """## REQ-STR-001 - Strict endpoint

<!-- trace:v1 id=REQ-STR-001 type=requirement -->

The endpoint must be traced.
""",
        "src/api.py": '''"""API module."""


# trace:v1 id=impl.str.endpoint work=WORK-STR-1 satisfies=REQ-STR-001
def public_endpoint():
    return "ok"
''',
        "tests/test_api.py": '''"""Tests."""


# trace:v1 id=test.str.endpoint verifies=REQ-STR-001 exercises=impl.str.endpoint
def test_endpoint():
    assert public_endpoint() == "ok"
''',
    }


def setup_strict_repo(tmp_path: Path) -> Path:
    """Repo with a fully traced chain under the strict policy profile."""
    root = make_git_repo(tmp_path, strict_files())
    _expect_ok(run_trace(root, "index", "--all"))
    return root


# ---------------------------------------------------------------------------
# DoD gap fixtures (chain + shapes)
# ---------------------------------------------------------------------------

_DOD_TRACE_TOML = """schema_version = 1
repo_id = "dod-chain"
cache_dir = ".trace/cache"

[index]
respect_gitignore = true
incremental = true
fts = true

[index.languages]
python = true
typescript = true
javascript = true
go = true
rust = true
java = true
cpp = false

[markers]
prefix = "trace:v1"
unknown_keys = "error"
"""


def chain_files() -> dict[str, str]:
    """Minimal requirement -> implementation -> test chain under the standard
    profile (committed config), used by the DoD gap tests."""
    return {
        ".gitignore": ".trace/cache/\n",
        ".trace/trace.toml": _DOD_TRACE_TOML,
        ".trace/policy.toml": """profile = "standard"

[lifecycle]
default = "wip"
ci = "merge"

[exclusions]
paths = ["vendor/**", "generated/**", "docs/vendor/**", ".trace/**"]
""",
        "docs/req.md": """## REQ-CHAIN-001 - Chain feature

<!-- trace:v1 id=REQ-CHAIN-001 type=requirement -->

The chain must be traced.
""",
        "src/chain.py": '''"""Chain module."""


# trace:v1 id=impl.chain.run satisfies=REQ-CHAIN-001
def run():
    """Run the chain."""
    return "ok"
''',
        "tests/test_chain.py": '''"""Tests."""


# trace:v1 id=test.chain.run verifies=REQ-CHAIN-001 exercises=impl.chain.run
def test_run():
    assert run() == "ok"
''',
    }


def shapes_files() -> dict[str, str]:
    """A class marker enclosing a method marker in one python file, plus the
    requirement the class satisfies.  The class body runs past the method
    (trailing attribute) so the enclosing line range strictly contains the
    method's, letting the indexer derive a ``contains`` structural edge."""
    return {
        ".gitignore": ".trace/cache/\n",
        ".trace/trace.toml": _DOD_TRACE_TOML,
        "docs/req.md": """## REQ-SHAPES-1 - Rectangle area

<!-- trace:v1 id=REQ-SHAPES-1 type=requirement -->

A rectangle must compute its area.
""",
        "src/shapes.py": '''"""Shape primitives."""


# trace:v1 id=impl.shapes.rectangle satisfies=REQ-SHAPES-1
class Rectangle:
    """A rectangle shape."""

    # trace:v1 id=impl.shapes.rectangle.area
    def area(self) -> int:
        return self.width * self.height

    default_scale = 1
''',
    }


def _expect_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def parse_json(proc: subprocess.CompletedProcess[str]) -> dict:
    """Parse a --json CLI invocation result."""
    assert proc.returncode in (0, 1), f"rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)
