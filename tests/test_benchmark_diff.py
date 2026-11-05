"""Tests for multi-run benchmark comparison and regression analysis (SF-EVAL-05)."""

import json
from pathlib import Path

from selfforge_agent.runtime.benchmark_diff import diff_eval_reports, format_eval_diff


def _make_report(
    *,
    manifest_sha: str = "3b4aa3fa",
    cases: int = 2,
    repeats: int = 2,
    by_case: dict[str, dict] | None = None,
    failure_reasons: dict[str, int] | None = None,
    duration: float = 100.0,
    tokens: dict[str, int] | None = None,
) -> dict:
    if by_case is None:
        by_case = {
            "case1": {"successes": 2, "runs": 2},
            "case2": {"successes": 1, "runs": 2},
        }
    if failure_reasons is None:
        failure_reasons = {"agent_max_steps": 1}
    if tokens is None:
        tokens = {"prompt": 1000, "completion": 200, "total": 1200}

    runs = sum(c["runs"] for c in by_case.values())
    successes = sum(c["successes"] for c in by_case.values())
    rate = successes / runs if runs else 0.0

    return {
        "version": 1,
        "manifest_sha256": manifest_sha,
        "cases": cases,
        "repeats": repeats,
        "runs": runs,
        "successes": successes,
        "success_rate": rate,
        "failure_reasons": failure_reasons,
        "by_case": by_case,
        "duration_seconds": duration,
        "tokens": tokens,
    }


def test_diff_eval_reports_basic_and_improved() -> None:
    rep_a = _make_report(
        by_case={"case1": {"successes": 2, "runs": 2}, "case2": {"successes": 1, "runs": 2}},
        failure_reasons={"agent_max_steps": 1},
        duration=120.0,
    )
    rep_b = _make_report(
        by_case={"case1": {"successes": 2, "runs": 2}, "case2": {"successes": 2, "runs": 2}},
        failure_reasons={},
        duration=90.0,
    )

    diff = diff_eval_reports(rep_a, rep_b)
    assert diff["manifest_mismatch"] is False
    assert diff["summary"]["successes"]["before"] == 3
    assert diff["summary"]["successes"]["after"] == 4
    assert diff["summary"]["successes"]["delta"] == 1
    assert diff["summary"]["success_rate"]["before"] == 0.75
    assert diff["summary"]["success_rate"]["after"] == 1.0
    assert diff["summary"]["success_rate"]["delta"] == 0.25
    assert diff["summary"]["duration_seconds"]["delta"] == -30.0

    assert len(diff["improved"]) == 1
    assert diff["improved"][0]["case"] == "case2"
    assert diff["improved"][0]["before"] == "1/2"
    assert diff["improved"][0]["after"] == "2/2"
    assert diff["improved"][0]["delta_rate"] == 0.5
    assert diff["regressed"] == []
    assert diff["unchanged"] == ["case1"]


def test_diff_eval_reports_regressed() -> None:
    rep_a = _make_report(
        by_case={"case1": {"successes": 2, "runs": 2}},
    )
    rep_b = _make_report(
        by_case={"case1": {"successes": 1, "runs": 2}},
        failure_reasons={"agent_timed_out": 1},
    )

    diff = diff_eval_reports(rep_a, rep_b)
    assert len(diff["regressed"]) == 1
    assert diff["regressed"][0]["case"] == "case1"
    assert diff["regressed"][0]["before"] == "2/2"
    assert diff["regressed"][0]["after"] == "1/2"
    assert diff["regressed"][0]["delta_rate"] == -0.5
    assert diff["improved"] == []


def test_diff_eval_reports_manifest_mismatch_and_case_diffs() -> None:
    rep_a = _make_report(
        manifest_sha="sha_a",
        by_case={"case1": {"successes": 1, "runs": 1}, "case_old": {"successes": 1, "runs": 1}},
    )
    rep_b = _make_report(
        manifest_sha="sha_b",
        by_case={"case1": {"successes": 1, "runs": 1}, "case_new": {"successes": 1, "runs": 1}},
    )

    diff = diff_eval_reports(rep_a, rep_b)
    assert diff["manifest_mismatch"] is True
    assert diff["added"] == ["case_new"]
    assert diff["removed"] == ["case_old"]


def test_diff_eval_reports_failure_reasons_matrix() -> None:
    rep_a = _make_report(
        failure_reasons={"agent_max_steps": 3, "agent_timed_out": 2},
    )
    rep_b = _make_report(
        failure_reasons={"agent_max_steps": 1, "holdout_assertion_failed": 1},
    )

    diff = diff_eval_reports(rep_a, rep_b)
    reasons = diff["failure_reasons"]
    assert reasons["agent_max_steps"]["before"] == 3
    assert reasons["agent_max_steps"]["after"] == 1
    assert reasons["agent_max_steps"]["delta"] == -2

    assert reasons["agent_timed_out"]["before"] == 2
    assert reasons["agent_timed_out"]["after"] == 0
    assert reasons["agent_timed_out"]["delta"] == -2

    assert reasons["holdout_assertion_failed"]["before"] == 0
    assert reasons["holdout_assertion_failed"]["after"] == 1
    assert reasons["holdout_assertion_failed"]["delta"] == 1


def test_format_eval_diff_markdown() -> None:
    rep_a = _make_report()
    rep_b = _make_report(by_case={"case1": {"successes": 2, "runs": 2}, "case2": {"successes": 2, "runs": 2}})
    diff = diff_eval_reports(rep_a, rep_b)

    md = format_eval_diff(diff, format="markdown")
    assert "# Benchmark Run Comparison" in md
    assert "Success Rate" in md
    assert "improved" in md.lower()
    assert "case2" in md

    txt = format_eval_diff(diff, format="text")
    assert "Benchmark Comparison" in txt
    assert "Success Rate" in txt


def test_cli_eval_diff(tmp_path: Path) -> None:
    from selfforge_agent.cli import main

    rep_a = _make_report(by_case={"case1": {"successes": 2, "runs": 2}})
    rep_b = _make_report(by_case={"case1": {"successes": 2, "runs": 2}})
    rep_regressed = _make_report(by_case={"case1": {"successes": 0, "runs": 2}})

    file_a = tmp_path / "a.json"
    file_b = tmp_path / "b.json"
    file_reg = tmp_path / "reg.json"
    out_file = tmp_path / "diff.md"

    file_a.write_text(json.dumps(rep_a), encoding="utf-8")
    file_b.write_text(json.dumps(rep_b), encoding="utf-8")
    file_reg.write_text(json.dumps(rep_regressed), encoding="utf-8")

    # Success comparison
    code = main(["eval-diff", str(file_a), str(file_b), "--output", str(out_file)])
    assert code == 0
    assert out_file.is_file()
    assert "Benchmark Run Comparison" in out_file.read_text(encoding="utf-8")

    # Fail on regression
    code_reg = main(["eval-diff", str(file_a), str(file_reg), "--fail-on-regression"])
    assert code_reg == 1


def test_api_benchmark_diff_endpoint() -> None:
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app

    client = TestClient(app)
    rep_a = _make_report(by_case={"case1": {"successes": 2, "runs": 2}})
    rep_b = _make_report(by_case={"case1": {"successes": 2, "runs": 2}})

    res = client.post("/api/benchmark/diff", json={
        "before_report": rep_a,
        "after_report": rep_b,
    })
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert data["manifest_mismatch"] is False

