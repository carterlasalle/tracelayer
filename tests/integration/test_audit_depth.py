"""Semantic auditor depth + plan sync tests (review: last two items)."""

from __future__ import annotations

from tests.conftest import make_git_repo, run_trace


# trace:v1 id=test.dogfood.tests.integration.test_audit_depth.py type=test
def _repo(tmp_path):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-1 - Auth\n\n<!-- trace:v1 id=REQ-1 type=requirement -->\n",
            "plan.md": (
                "## PLAN-1 - Implement\n\n"
                "<!-- trace:v1 id=PLAN-1 type=plan expects=impl.one,impl.gone -->\n"
            ),
            "src/app.py": (
                "# trace:v1 id=impl.one satisfies=REQ-1 implements=PLAN-1\n"
                "def rotate_refresh_token(t):\n    return f'rotated-{t}'\n"
            ),
            "test_a.py": (
                "# trace:v1 id=test.one verifies=REQ-1 exercises=impl.one\n"
                "def test_rotation():\n    assert 'x' == 'x'\n"
            ),
        },
    )
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "index", "--all")
    return repo


def test_package_findings_surface_misleading_test_and_plan_gap(tmp_path):
    """The auditor package names meaning-adjacent suspects deterministically."""
    repo = _repo(tmp_path)
    r = run_trace(repo, "audit", "package")
    assert r.returncode == 0, r.stderr
    import json

    package = json.loads(r.stdout)
    kinds = {f["kind"] for f in package["deterministic_findings"]}
    assert "suspected_misleading_test" in kinds  # test.one never names impl symbol
    assert "plan_gap" in kinds  # PLAN-1 expects impl.gone which does not exist
    gap = next(f for f in package["deterministic_findings"] if f["kind"] == "plan_gap")
    assert gap["missing"] == "impl.gone"


def test_plan_sync_reports_and_applies(tmp_path):
    """Plan sync discovers built artifacts and can write them into the marker."""
    repo = _repo(tmp_path)
    # build a second implementation linking the plan but not declared
    (repo / "src" / "two.py").write_text(
        "# trace:v1 id=impl.two satisfies=REQ-1 implements=PLAN-1\ndef second(t):\n    return t\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    r = run_trace(repo, "plan", "sync", "PLAN-1")
    assert r.returncode != 0  # out of sync
    assert "discovered: ['impl.two']" in r.stdout
    assert "impl.gone" in r.stdout and "missing" in r.stdout
    r = run_trace(repo, "plan", "sync", "PLAN-1", "--apply")
    assert r.returncode == 0, r.stdout
    assert "expects=impl.gone,impl.one,impl.two" in r.stdout
    marker = (repo / "plan.md").read_text(encoding="utf-8")
    assert "expects=impl.gone,impl.one,impl.two" in marker
    # after apply the plan is in sync (impl.gone still expected -> TL014 blocks,
    # but plan status no longer reports discovery drift)
    r = run_trace(repo, "plan", "sync", "PLAN-1")
    assert r.returncode != 0  # impl.gone still missing (declared)
    assert "discovered: (none)" in r.stdout
    assert "impl.gone" in r.stdout and "missing" in r.stdout
