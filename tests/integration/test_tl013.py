"""TL013 behavior-boundary coverage integration tests (review P1)."""

from __future__ import annotations

from tests.conftest import make_git_repo, run_trace

BASE = {
    "req.md": "## REQ-1 - Auth\n\n<!-- \x74race:v1 id=REQ-1 type=requirement -->\n",
    "src/app.py": "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef keep():\n    return 1\n",
}


# trace:v1 id=test.dogfood.tests.integration.test_tl013.py type=test
def _repo(tmp_path, extra=None):
    files = dict(BASE)
    if extra:
        files.update(extra)
    repo = make_git_repo(tmp_path, files)
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "index", "--all")
    return repo


def _verify(repo):
    return run_trace(repo, "verify", "--changed", "--lifecycle", "merge")


def test_one_marker_does_not_cover_new_untraced_boundary(tmp_path):
    """The review's escape hatch: a claimed file with a new untraced function."""
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef keep():\n    return 1\n\n\n"
        "def new_payment_flow():\n    return 2\n",
        encoding="utf-8",
    )
    proc = _verify(repo)
    assert proc.returncode != 0
    assert "TL013" in proc.stdout
    assert "new_payment_flow" in proc.stdout


def test_new_boundary_with_marker_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef keep():\n    return 1\n\n\n"
        "# \x74race:v1 id=impl.payments work=WORK-1 satisfies=REQ-1\ndef new_payment_flow():\n    return 2\n",
        encoding="utf-8",
    )
    (repo / ".trace").mkdir(parents=True, exist_ok=True)
    (repo / ".trace" / "work.toml").write_text('[work."WORK-1"]\ntitle = "Payments"\n')
    run_trace(repo, "index", "--all")
    proc = _verify(repo)
    assert "TL013" not in proc.stdout, proc.stdout


def test_method_without_explicit_inheritance_is_traced_individually(tmp_path):
    """A class marker does NOT geometrically cover its methods: each method
    is a boundary unless it declares `# trace:inherit <id> reason=...`."""
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "# trace:v1 id=impl.one satisfies=REQ-1\nclass Service:\n"
        "    def keep(self):\n        return 1\n\n"
        "    def new_method(self):\n        return 2\n",
        encoding="utf-8",
    )
    proc = _verify(repo)
    assert "TL013" in proc.stdout  # the methods are individually untraced
    assert "new_method" in proc.stdout
    # Explicit inheritance declares the method subordinate to the class.
    (repo / "src" / "app.py").write_text(
        "# trace:v1 id=impl.one satisfies=REQ-1\nclass Service:\n"
        "    # trace:inherit impl.one reason=implementation-detail\n"
        "    def keep(self):\n        return 1\n\n"
        "    # trace:inherit impl.one reason=implementation-detail\n"
        "    def new_method(self):\n        return 2\n",
        encoding="utf-8",
    )
    proc = _verify(repo)
    assert "TL013" not in proc.stdout, proc.stdout


# trace:v1 id=test.dogfood.tests.integration.test_tl013.exempt type=test
def test_exempt_boundary_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "# \x74race:v1 id=impl.one satisfies=REQ-1\ndef keep():\n    return 1\n\n\n"
        "# trace:exempt reason=trivial-helper\ndef trivial_helper():\n    return 0\n",
        encoding="utf-8",
    )
    proc = _verify(repo)
    assert "TL013" not in proc.stdout, proc.stdout


def test_new_config_key_requires_trace(tmp_path):
    """Config classifiers: a new top-level key is a behavioral boundary."""
    repo = _repo(tmp_path, {"config.yaml": "server:\n  port: 8080\n"})
    (repo / "config.yaml").write_text("server:\n  port: 8080\nnew_contract:\n  enabled: true\n")
    proc = _verify(repo)
    assert "TL013" in proc.stdout
    assert "new_contract" in proc.stdout
    # A marker directly above the key attaches to it (per-key, not file-level).
    (repo / "config.yaml").write_text(
        "server:\n  port: 8080\n# trace:v1 id=ops.config.contract work=WORK-1\nnew_contract:\n  enabled: true\n"
    )
    (repo / ".trace").mkdir(parents=True, exist_ok=True)
    (repo / ".trace" / "work.toml").write_text('[work."WORK-1"]\ntitle = "Config"\n')
    run_trace(repo, "index", "--all")
    proc = _verify(repo)
    assert "TL013" not in proc.stdout, proc.stdout


def test_new_markdown_heading_boundary(tmp_path):
    """Docs classifiers: a new non-node heading is a boundary."""
    repo = _repo(tmp_path, {"docs.md": "# Existing\n\nsome prose\n"})
    (repo / "docs.md").write_text("# Existing\n\nsome prose\n\n## New Section\n\nmore\n")
    proc = _verify(repo)
    assert "TL013" in proc.stdout
    assert "New Section" in proc.stdout
    # A REQ-/ADR-style heading infers a node without a marker.
    (repo / "docs.md").write_text("# Existing\n\nsome prose\n\n## ADR-9 - New decision\n\nmore\n")
    proc = _verify(repo)
    assert "TL013" not in proc.stdout, proc.stdout
