"""TL014 plan-level obligations + plan status integration tests (review P2)."""

from __future__ import annotations

from tests.conftest import make_git_repo, run_trace


# trace:v1 id=test.dogfood.tests.integration.test_tl014.py type=test
def _repo(tmp_path, plan_expects: str):
    repo = make_git_repo(
        tmp_path,
        {
            "req.md": "## REQ-1 - Auth\n\n<!-- trace:v1 id=REQ-1 type=requirement -->\n",
            "plan.md": (
                f"## PLAN-1 - Implement\n\n<!-- trace:v1 id=PLAN-1 type=plan {plan_expects} -->\n"
            ),
            "src/app.py": (
                "# trace:v1 id=impl.one satisfies=REQ-1 implements=PLAN-1\ndef one():\n    return 1\n"
            ),
        },
    )
    run_trace(repo, "init", "--no-skill", "--no-mcp")
    run_trace(repo, "index", "--all")
    return repo


def test_missing_expected_artifact_blocks(tmp_path):
    repo = _repo(tmp_path, "expects=impl.one,impl.two")
    proc = run_trace(repo, "verify", "--all", "--lifecycle", "merge")
    assert proc.returncode != 0
    assert "TL014" in proc.stdout
    assert "impl.two" in proc.stdout


def test_expected_artifact_without_implements_edge_blocks(tmp_path):
    repo = _repo(tmp_path, "expects=impl.one,impl.three")
    (repo / "src" / "three.py").write_text(
        "# trace:v1 id=impl.three satisfies=REQ-1\ndef three():\n    return 3\n",
        encoding="utf-8",
    )
    run_trace(repo, "index", "--all")
    proc = run_trace(repo, "verify", "--all", "--lifecycle", "merge")
    assert proc.returncode != 0
    assert "TL014" in proc.stdout
    assert "implements" in proc.stdout


def test_all_expected_artifacts_pass_and_plan_status(tmp_path):
    repo = _repo(tmp_path, "expects=impl.one")
    proc = run_trace(repo, "verify", "--all", "--lifecycle", "merge")
    assert "TL014" not in proc.stdout, proc.stdout
    status = run_trace(repo, "plan", "status", "PLAN-1")
    assert status.returncode == 0, status.stdout
    assert "impl.one: ok" in status.stdout
    assert "all expected artifacts present" in status.stdout
