"""Configuration models and loading.

Core config: `.trace/trace.toml` (spec Section 49). Policy config:
`.trace/policy.toml` (spec Section 24.4). Loaded with actionable TL100
diagnostics; defaults apply when files are absent (observe mode).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracelayer.diagnostics import Diagnostic, make

CACHE_DIR = ".trace/cache"


# --------------------------------------------------------------------------
# Trace config (.trace/trace.toml)
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail
class IndexLanguages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python: bool = True
    typescript: bool = True
    javascript: bool = True
    go: bool = True
    rust: bool = True
    java: bool = True
    cpp: bool = False


# trace:exempt reason=internal-detail
class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    respect_gitignore: bool = True
    incremental: bool = True
    fts: bool = True
    languages: IndexLanguages = Field(default_factory=IndexLanguages)


# trace:exempt reason=internal-detail
class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".trace/cache/**",
            "node_modules/**",
            ".venv/**",
            "dist/**",
            "build/**",
        ]
    )
    generated: list[str] = Field(default_factory=lambda: ["src/generated/**"])


# trace:exempt reason=internal-detail
class MarkersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = "trace:v1"
    unknown_keys: str = "error"  # error | warning | permissive


# trace:exempt reason=internal-detail
class HooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_context_chars: int = 1500
    pre_edit_require_context: bool = True
    pre_edit_block_once: bool = True
    prompt_search_limit: int = 5


# trace:exempt reason=internal-detail
class EvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_revision: bool = True
    preferred_coverage_proof: str = "suite"  # suite | per_test


# trace:exempt reason=internal-detail
class ExternalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolve_by_default: bool = False


# trace:exempt reason=internal-detail
class TraceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    repo_id: str = ""
    cache_dir: str = CACHE_DIR
    index: IndexConfig = Field(default_factory=IndexConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    markers: MarkersConfig = Field(default_factory=MarkersConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    external: ExternalConfig = Field(default_factory=ExternalConfig)
    # Optional monorepo scopes: name -> list of path prefixes (NFR-013).
    scopes: dict[str, list[str]] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Policy config (.trace/policy.toml)
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail
class RequirementsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_work_ancestry: bool = False
    require_requirement_for_changed_behavior: bool = False
    require_verifying_test: bool = False
    require_test_pass: bool = False
    require_coverage_confirmation: bool = False
    require_execution_evidence: bool = False
    block_stale: bool = False
    require_semantic_audit: bool = False
    require_audit_records: bool = False
    allow_waivers: bool = True


# trace:exempt reason=internal-detail
class ExclusionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(default_factory=list)


# trace:exempt reason=internal-detail
class Waiver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    trace_id: str | None = None
    path: str | None = None
    reason: str = ""
    expires: date | None = None
    owner: str | None = None

    def expired(self, today: date | None = None) -> bool:
        if self.expires is None:
            return False
        return self.expires < (today or date.today())

    def matches(self, rule_id: str, trace_id: str | None, path: str | None) -> bool:
        if self.rule != rule_id:
            return False
        if self.trace_id is not None and self.trace_id != trace_id:
            return False
        if self.path is not None and (path is None or self.path != path):
            return False
        return True


# trace:exempt reason=internal-detail
class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = "standard"  # minimal | standard | strict | safety-critical
    lifecycle: dict[str, str] = Field(default_factory=lambda: {"default": "wip", "ci": "merge"})
    requirements: dict[str, RequirementsConfig] = Field(default_factory=dict)
    exclusions: ExclusionsConfig = Field(default_factory=ExclusionsConfig)
    waivers: list[Waiver] = Field(default_factory=list)

    def lifecycle_for(self, requested: str | None, *, ci: bool = False) -> str:
        if requested:
            return requested
        if ci and "ci" in self.lifecycle:
            return self.lifecycle["ci"]
        return self.lifecycle.get("default", "wip")


# --------------------------------------------------------------------------
# Project wrapper and loading
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail
@dataclass
class Project:
    root: Path
    config: TraceConfig
    policy: PolicyConfig | None = None

    @property
    def cache_dir(self) -> Path:
        return self.root / self.config.cache_dir

    @property
    def db_path(self) -> Path:
        return self.cache_dir / "index.sqlite3"

    @property
    def session_dir(self) -> Path:
        return self.cache_dir / "session"


# trace:exempt reason=internal-detail
def find_repo_root(start: Path | None = None) -> Path:
    """Walk up for `.trace/trace.toml`, else `.git`, else the start directory."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".trace" / "trace.toml").exists() or (candidate / ".git").exists():
            return candidate
    return current


# trace:exempt reason=internal-detail
def load_project(root: Path | None = None) -> tuple[Project, list[Diagnostic]]:
    root = (root or find_repo_root()).resolve()
    diags: list[Diagnostic] = []
    cfg_path = root / ".trace" / "trace.toml"
    if cfg_path.exists():
        try:
            data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
            config = TraceConfig(**data)
        except (tomllib.TOMLDecodeError, ValidationError) as exc:
            diags.append(
                make(
                    "TL100",
                    path=str(cfg_path),
                    message=f"Cannot parse {cfg_path.relative_to(root)}: {_compact(exc)}",
                )
            )
            config = TraceConfig(repo_id=root.name)
    else:
        diags.append(
            make(
                "TL100",
                severity="INFO",
                message="No .trace/trace.toml found; using defaults (run `trace init`)",
            )
        )
        config = TraceConfig(repo_id=root.name)
    if not config.repo_id:
        config.repo_id = root.name
    policy = load_policy(root, diags)
    return Project(root=root, config=config, policy=policy), diags


# trace:exempt reason=internal-detail
def load_policy(root: Path, diags: list[Diagnostic] | None = None) -> PolicyConfig | None:
    path = root / ".trace" / "policy.toml"
    if not path.exists():
        # No policy file: the default policy (standard profile) still
        # governs, so its exclusions apply to unconfigured repositories.
        return PolicyConfig(**tomllib.loads(default_policy_toml()))
    diags = diags if diags is not None else []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return PolicyConfig(**data)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        diags.append(
            make(
                "TL100",
                path=str(path),
                message=f"Cannot parse {path.relative_to(root)}: {_compact(exc)}",
            )
        )
        return PolicyConfig()


def _compact(exc: Exception) -> str:
    """Compact, actionable error text from pydantic/tomllib exceptions."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:8]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg', 'invalid')}")
        return "; ".join(parts)
    text = str(exc).strip().replace("\n", " ")
    return text[:300]


# --------------------------------------------------------------------------
# Default file contents written by `trace init`
# --------------------------------------------------------------------------


# trace:exempt reason=internal-detail
def default_trace_toml(repo_id: str) -> str:
    return f"""# TraceLayer configuration (spec Section 49).
schema_version = 1
repo_id = "{repo_id}"
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

[discovery]
include = ["**/*"]
exclude = [
  ".git/**",
  ".trace/cache/**",
  "node_modules/**",
  ".venv/**",
  "dist/**",
  "build/**"
]
generated = ["src/generated/**"]

[markers]
prefix = "trace:v1"
unknown_keys = "error"

[hooks]
max_context_chars = 1500
pre_edit_require_context = true
pre_edit_block_once = true
prompt_search_limit = 5

[evidence]
require_revision = true
preferred_coverage_proof = "suite"

[external]
resolve_by_default = false
"""


# trace:v1 id=impl.config.default-policy work=WORK-TL-001
def default_policy_toml() -> str:
    return """# TraceLayer policy (spec Section 24.4).
profile = "standard"

[lifecycle]
default = "wip"
ci = "merge"

[requirements.merge]
require_work_ancestry = true
require_requirement_for_changed_behavior = true
require_verifying_test = true
require_test_pass = true
require_coverage_confirmation = false
block_stale = true

[requirements.release]
require_coverage_confirmation = true
require_semantic_audit = true

[exclusions]
paths = [
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
  ".agents/**",
  ".claude/**",
  ".codex/**",
  ".pi/**",
  ".omp/**",
  ".hermes/**",
  ".serena/**",
  "opencode.json",
  "tests/**",
  ".agents/skills/**",
  ".claude/skills/**",
  ".pi/skills/**",
  ".omp/skills/**",
  ".hermes/skills/**",
  "*_test.*",
  "*.test.*",
  "*_spec.*",
  "*.spec.*"
]
"""
