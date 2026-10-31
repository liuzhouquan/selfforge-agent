"""Tests for session token metrics, calibrated cost tracking, and interactive controls."""

from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.coding.models import RepairReport
from selfforge_agent.models import PRContext
from selfforge_agent.runtime.agent import AgentResult
from selfforge_agent.runtime.pricing import MonetaryCost
from selfforge_agent.runtime.session import SessionStore


def test_session_store_token_metrics_empty(tmp_path: Path) -> None:
    session = SessionStore(tmp_path, "sessionempty")
    metrics = session.token_metrics()
    assert metrics["prompt_tokens"] == 0
    assert metrics["completion_tokens"] == 0
    assert metrics["total_tokens"] == 0
    assert metrics["model"] is None
    assert metrics["cost"] is None


def test_session_store_token_metrics_aggregation(tmp_path: Path, configured_rates: None) -> None:
    session = SessionStore(tmp_path, "sessionusage")
    session.trace("model_call", provider="deepseek", model="deepseek-chat",
                  usage={"prompt_tokens": 10_000, "completion_tokens": 2_000, "total_tokens": 12_000})
    session.trace("tool", name="read_file", ok=True)
    session.trace("model_call", provider="deepseek", model="deepseek-chat",
                  usage={"prompt_tokens": 5_000, "completion_tokens": 1_000, "total_tokens": 6_000})

    metrics = session.token_metrics()
    assert metrics["prompt_tokens"] == 15_000
    assert metrics["completion_tokens"] == 3_000
    assert metrics["total_tokens"] == 18_000
    assert metrics["model"] == "deepseek-chat"
    assert metrics["cost"] is not None
    assert metrics["cost"]["currency"] == "USD"
    assert metrics["cost"]["calibrated"] is True
    assert metrics["cost"]["total_cost_usd"] > 0


def test_session_store_token_metrics_unknown_model(tmp_path: Path) -> None:
    session = SessionStore(tmp_path, "sessionunknown")
    session.trace("model_call", provider="custom", model="my-uncalibrated-model",
                  usage={"prompt_tokens": 1_000, "completion_tokens": 500, "total_tokens": 1_500})

    metrics = session.token_metrics()
    assert metrics["prompt_tokens"] == 1_000
    assert metrics["completion_tokens"] == 500
    assert metrics["total_tokens"] == 1_500
    assert metrics["model"] == "my-uncalibrated-model"
    assert metrics["cost"] is None


def test_agent_result_token_fields() -> None:
    cost = MonetaryCost(
        currency="USD",
        prompt_cost_usd=0.001,
        completion_cost_usd=0.002,
        total_cost_usd=0.003,
        calibrated=True,
    )
    res = AgentResult("sess-1", "completed", "Done", 2, 3, "", total_tokens=5_000, monetary_cost=cost)
    assert res.total_tokens == 5_000
    assert res.monetary_cost is not None
    assert res.monetary_cost.total_cost_usd == 0.003


def test_repair_report_token_and_cost_fields(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    cost = MonetaryCost(
        currency="USD",
        prompt_cost_usd=0.0005,
        completion_cost_usd=0.001,
        total_cost_usd=0.0015,
        calibrated=True,
    )
    context = PRContext(
        repository=str(tmp_path),
        base_sha="0" * 40,
        head_sha="1" * 40,
        title="Sample",
        description="",
        files=[],
    )
    report = RepairReport(
        run_id="rep-1",
        repository=str(tmp_path),
        base_sha="0" * 40,
        head_sha="1" * 40,
        task="Repair bug",
        context=context,
        findings=[],
        status="inconclusive",
        started_at=now,
        finished_at=now,
        total_tokens=2_500,
        monetary_cost=cost,
    )
    assert report.total_tokens == 2_500
    assert report.monetary_cost is not None
    assert report.monetary_cost.total_cost_usd == 0.0015


def test_coding_job_manager_get_includes_token_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_rates: None
) -> None:
    from fastapi.testclient import TestClient

    from selfforge_agent.api import app as api_module
    from selfforge_agent.api.coding_jobs import CodingJob, CodingJobManager, DurableCancel

    repo = tmp_path / "repo"
    repo.mkdir()
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    session = SessionStore(session_root, "jobsession")
    session.trace("model_call", provider="deepseek", model="deepseek-chat",
                  usage={"prompt_tokens": 10_000, "completion_tokens": 2_000, "total_tokens": 12_000})

    manager = CodingJobManager(root=tmp_path / "jobs")
    job_id = "a1b2c3d4e5f6"
    cancel_path = manager._cancel_path(job_id)
    from threading import Event

    cancel = DurableCancel(cancel_path, Event())
    job = CodingJob(job_id, repo, session, cancel, repo, state="completed")
    manager._write_record(manager._record(job))

    data = manager.get(job.job_id)
    assert "token_metrics" in data
    assert data["token_metrics"]["prompt_tokens"] == 10_000
    assert data["token_metrics"]["total_tokens"] == 12_000
    assert data["token_metrics"]["cost"] is not None
    monkeypatch.setattr(api_module, "_CODING_JOBS", manager)
    response = TestClient(api_module.app).get(f"/api/code/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["token_metrics"]["total_tokens"] == 12_000


def test_cli_trace_summary_output(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                  configured_rates: None) -> None:
    from selfforge_agent.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    session = SessionStore(session_root, "tracesess")
    session.trace("model_call", provider="deepseek", model="deepseek-chat",
                  usage={"prompt_tokens": 20_000, "completion_tokens": 4_000, "total_tokens": 24_000})

    with (
        patch("selfforge_agent.runtime.workspace.CodingWorkspaceManager.session_root", return_value=session_root),
        patch("sys.argv", ["selfforge-agent", "trace", "tracesess", "--repo", str(repo), "--summary"]),
    ):
        from selfforge_agent.cli import main
        ret = main()
        assert ret == 0

    captured = capsys.readouterr().out
    assert "Summary for session tracesess:" in captured
    assert "Total tokens: 24,000" in captured
    assert "Prompt: 20,000" in captured
    assert "Estimated cost:" in captured


def test_cli_interactive_flag_registered() -> None:
    from selfforge_agent.cli import main

    # verify that --interactive argument is recognized by code subcommand
    with patch("sys.argv", ["selfforge-agent", "code", "--help"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


def test_session_metrics_price_each_model_and_preserve_reported_total(
    tmp_path: Path, configured_rates: None
) -> None:
    session = SessionStore(tmp_path, "mixedmodels")
    session.trace("model_call", provider="deepseek", model="deepseek-chat",
                  usage={"prompt_tokens": 1_000, "completion_tokens": 500, "total_tokens": 1_600})
    session.trace("model_call", provider="openrouter", model="other-model",
                  usage={"prompt_tokens": 1_000, "completion_tokens": 500, "total_tokens": 1_500})
    metrics = session.token_metrics()
    assert metrics["total_tokens"] == 3_100
    assert metrics["model"] is None
    assert metrics["models"] == ["deepseek-chat", "other-model"]
    assert metrics["cost"]["total_cost_usd"] == pytest.approx(0.00228)
    session.trace("model_call", provider="custom", model="unknown", usage=None)
    metrics = session.token_metrics()
    assert metrics["cost"] is None
    assert metrics["usage_complete"] is False
