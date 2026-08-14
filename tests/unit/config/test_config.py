"""Unit tests for tracelayer.config: loading, defaults, policy, waivers.

Covers load_project default-vs-explicit behavior, TL100 on malformed TOML
and pydantic errors, policy.toml parsing, waiver matching/expiry, and
lifecycle resolution.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tracelayer.config import (
    PolicyConfig,
    TraceConfig,
    Waiver,
    default_policy_toml,
    default_trace_toml,
    load_policy,
    load_project,
)
from tracelayer.diagnostics import SEVERITY_ERROR, SEVERITY_INFO

TRACE_TOML = """schema_version = 1
repo_id = "my-repo"

[index]
respect_gitignore = false
incremental = false
fts = false

[index.languages]
python = true
typescript = false

[discovery]
include = ["src/**"]
exclude = ["src/generated/**"]

[markers]
prefix = "\x74race:v1"
unknown_keys = "warning"

[hooks]
max_context_chars = 999

[evidence]
require_revision = false
preferred_coverage_proof = "per_test"

[external]
resolve_by_default = true

[scopes]
core = ["src/core"]
"""


# trace:v1 id=test.dogfood.tests.unit.config.test_config.py type=test
def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_load_project_defaults_when_absent(tmp_path: Path) -> None:
    project, diags = load_project(tmp_path)
    assert project.root == tmp_path.resolve()
    assert project.config.repo_id == tmp_path.name
    assert project.config.cache_dir == ".trace/cache"
    assert project.config.index.respect_gitignore is True
    assert project.config.index.languages.python is True
    assert project.config.index.languages.cpp is False
    assert project.policy is not None
    assert project.policy.profile == "standard"
    assert len(diags) == 1
    assert diags[0].rule_id == "TL100"
    assert diags[0].severity == SEVERITY_INFO
    assert "trace init" in diags[0].message


def test_load_project_explicit_trace_toml(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", TRACE_TOML)
    project, diags = load_project(tmp_path)
    assert project.config.repo_id == "my-repo"
    assert project.config.index.respect_gitignore is False
    assert project.config.index.languages.typescript is False
    assert project.config.discovery.include == ["src/**"]
    assert project.config.markers.unknown_keys == "warning"
    assert project.config.hooks.max_context_chars == 999
    assert project.config.evidence.require_revision is False
    assert project.config.evidence.preferred_coverage_proof == "per_test"
    assert project.config.external.resolve_by_default is True
    assert project.config.scopes == {"core": ["src/core"]}
    assert diags == []


def test_load_project_malformed_toml_emits_tl100(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", "schema_version = [unclosed")
    project, diags = load_project(tmp_path)
    assert project.config.repo_id == tmp_path.name  # defaults applied
    assert len(diags) == 1
    d = diags[0]
    assert d.rule_id == "TL100"
    assert d.severity == SEVERITY_ERROR
    assert d.path is not None and d.path.endswith("trace.toml")


def test_load_project_pydantic_error_emits_tl100(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", "schema_version = 'not-an-int'\nrepo_id = 'x'")
    project, diags = load_project(tmp_path)
    # On any pydantic error the whole config falls back to defaults.
    assert project.config.repo_id == tmp_path.name
    assert project.config.schema_version == 1
    assert len(diags) == 1
    assert diags[0].rule_id == "TL100"
    assert diags[0].severity == SEVERITY_ERROR
    assert "schema_version" in diags[0].message


def test_load_project_unknown_key_emits_tl100(tmp_path: Path) -> None:
    # TraceConfig uses extra="forbid": an unknown top-level key is a
    # pydantic ValidationError, surfaced as TL100 with default fallback.
    _write(tmp_path, ".trace/trace.toml", "repo_id = 'x'\nbogus_key = 1")
    project, diags = load_project(tmp_path)
    assert project.config.repo_id == tmp_path.name
    assert len(diags) == 1
    assert diags[0].rule_id == "TL100"
    assert "bogus_key" in diags[0].message


def test_load_project_empty_repo_id_defaults_to_root_name(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", "[index]\nfts = true\n")
    project, diags = load_project(tmp_path)
    assert project.config.repo_id == tmp_path.name
    assert project.config.index.fts is True


def test_load_policy_absent_uses_default(tmp_path: Path) -> None:
    """No policy file: the default policy still governs (TL012 exclusions
    must apply to unconfigured repositories)."""
    policy = load_policy(tmp_path)
    assert policy is not None
    assert policy.profile == "standard"
    assert ".trace/**" in policy.exclusions.paths


def test_load_policy_parse(tmp_path: Path) -> None:
    policy_text = """profile = "strict"

[lifecycle]
default = "wip"
ci = "release"

[requirements.merge]
require_work_ancestry = true
require_verifying_test = true

[exclusions]
paths = ["vendor/**"]

[[waivers]]
rule = "TL010"
trace_id = "REQ-1"
reason = "known gap"
expires = "2027-01-01"
owner = "platform"
"""
    _write(tmp_path, ".trace/policy.toml", policy_text)
    policy = load_policy(tmp_path)
    assert policy is not None
    assert policy.profile == "strict"
    assert policy.lifecycle == {"default": "wip", "ci": "release"}
    assert policy.requirements["merge"].require_work_ancestry is True
    assert policy.requirements["merge"].require_verifying_test is True
    assert policy.requirements["merge"].require_test_pass is False
    assert policy.exclusions.paths == ["vendor/**"]
    assert len(policy.waivers) == 1
    w = policy.waivers[0]
    assert w.rule == "TL010"
    assert w.trace_id == "REQ-1"
    assert w.expires == date(2027, 1, 1)
    assert w.owner == "platform"


def test_load_policy_malformed_emits_tl100_and_falls_back(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/policy.toml", "profile = [broken")
    diags: list = []
    policy = load_policy(tmp_path, diags)
    assert policy is not None
    assert policy.profile == "standard"  # PolicyConfig() defaults
    assert len(diags) == 1
    assert diags[0].rule_id == "TL100"
    assert diags[0].severity == SEVERITY_ERROR


def test_load_project_includes_policy(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", "repo_id = 'r'")
    _write(tmp_path, ".trace/policy.toml", 'profile = "minimal"\n')
    project, diags = load_project(tmp_path)
    assert project.policy is not None
    assert project.policy.profile == "minimal"
    assert diags == []


def test_waiver_matches_rule_and_scopes() -> None:
    w = Waiver(rule="TL010", reason="r")
    assert w.matches("TL010", None, None) is True
    assert w.matches("TL011", None, None) is False

    w2 = Waiver(rule="TL010", trace_id="REQ-1", reason="r")
    assert w2.matches("TL010", "REQ-1", None) is True
    assert w2.matches("TL010", "REQ-2", None) is False

    w3 = Waiver(rule="TL010", path="src/auth.py", reason="r")
    assert w3.matches("TL010", None, "src/auth.py") is True
    # A path-scoped waiver requires the evaluated path to be present.
    assert w3.matches("TL010", None, None) is False
    assert w3.matches("TL010", None, "src/other.py") is False

    w4 = Waiver(rule="TL010", trace_id="REQ-1", path="src/auth.py", reason="r")
    assert w4.matches("TL010", "REQ-1", "src/auth.py") is True
    assert w4.matches("TL010", "REQ-1", "src/other.py") is False
    assert w4.matches("TL010", "REQ-2", "src/auth.py") is False


def test_waiver_expiry() -> None:
    past = date(2020, 1, 1)
    future = date(2030, 1, 1)
    today = date(2024, 6, 15)

    assert Waiver(rule="TL010", expires=None, reason="").expired(today) is False
    assert Waiver(rule="TL010", expires=past, reason="").expired(today) is True
    assert Waiver(rule="TL010", expires=future, reason="").expired(today) is False
    # Boundary: expires == today is not expired.
    assert Waiver(rule="TL010", expires=today, reason="").expired(today) is False
    # Default argument uses date.today(); passing a past date keeps it
    # deterministic without a fixed clock.
    assert Waiver(rule="TL010", expires=past, reason="").expired() is True


def test_lifecycle_for_resolution() -> None:
    policy = PolicyConfig()  # default lifecycle {"default": "wip", "ci": "merge"}
    assert policy.lifecycle_for(None) == "wip"
    assert policy.lifecycle_for(None, ci=True) == "merge"
    assert policy.lifecycle_for("release") == "release"
    assert policy.lifecycle_for("", ci=True) == "merge"

    custom = PolicyConfig(lifecycle={"default": "review", "ci": "release"})
    assert custom.lifecycle_for(None) == "review"
    assert custom.lifecycle_for(None, ci=True) == "release"

    # Missing ci key falls back to default.
    no_ci = PolicyConfig(lifecycle={"default": "draft"})
    assert no_ci.lifecycle_for(None, ci=True) == "draft"


def test_project_derived_paths(tmp_path: Path) -> None:
    project, _ = load_project(tmp_path)
    assert project.cache_dir == tmp_path.resolve() / ".trace" / "cache"
    assert project.db_path == project.cache_dir / "index.sqlite3"
    assert project.session_dir == project.cache_dir / "session"


def test_default_trace_toml_roundtrip(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/trace.toml", default_trace_toml("roundtrip-repo"))
    project, diags = load_project(tmp_path)
    assert diags == []
    assert project.config.repo_id == "roundtrip-repo"
    assert project.config.schema_version == 1
    assert project.config.index.languages.python is True
    assert project.config.discovery.include == ["**/*"]
    assert project.config.markers.prefix == "\x74race:v1"


def test_default_policy_toml_roundtrip(tmp_path: Path) -> None:
    _write(tmp_path, ".trace/policy.toml", default_policy_toml())
    policy = load_policy(tmp_path)
    assert policy is not None
    assert policy.profile == "standard"
    assert policy.lifecycle == {"default": "wip", "ci": "merge"}
    assert policy.requirements["merge"].require_work_ancestry is True
    assert policy.requirements["merge"].block_stale is True
    assert policy.requirements["release"].require_semantic_audit is True
    assert policy.exclusions.paths == [
        "vendor/**",
        "generated/**",
        "docs/vendor/**",
        ".trace/**",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        ".mcp.json",
        "junit.xml",
        "coverage.xml",
    ]


def test_trace_config_constructs_defaults() -> None:
    cfg = TraceConfig(repo_id="r")
    assert cfg.schema_version == 1
    assert cfg.index.fts is True
    assert cfg.markers.unknown_keys == "error"
    assert cfg.evidence.require_revision is True
    assert cfg.scopes == {}
