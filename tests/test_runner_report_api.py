from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verified_pr_agent.api.app import app
from verified_pr_agent.service import ReviewService
from verified_pr_agent.verification.runner import TestRunner, TrustRequiredError


def test_pytest_pass_fail_and_trust(demo_repo: tuple[Path, str, str]) -> None:
    repo, _, _ = demo_repo
    runner = TestRunner()
    with pytest.raises(TrustRequiredError):
        runner.run(repo)
    failure = runner.run(repo, trusted=True)
    assert failure.status == "failed"
    assert failure.exit_code == 1
    assert failure.failed == 1
    assert failure.passed == 0
    assert "python=" in failure.environment
    (repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    success = runner.run(repo, trusted=True)
    assert success.status == "passed"
    assert success.exit_code == 0
    assert success.passed == 1
    assert success.failed == 0


def test_timeout(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n"
    )
    result = TestRunner().run(tmp_path, trusted=True, timeout=0.2)
    assert result.status == "timeout"
    assert result.exit_code is None
    assert result.error_message is not None


def test_report_json_keeps_findings_suspected(demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    report = ReviewService().review(repo, base, head, trusted_tests=True)
    restored = type(report).model_validate_json(report.model_dump_json())
    assert restored.run_id
    assert restored.repository == str(repo)
    assert restored.test_results[0].failed == 1
    assert restored.findings[0].verification_status == "suspected"
    assert restored.execution_status == "completed"
    assert restored.finished_at >= restored.started_at


def test_api_requires_configured_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIFIED_PR_AGENT_REPO_ROOT", raising=False)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/review", json={"base": "a" * 40, "head": "b" * 40}).status_code == 503


def test_api_review_no_test_execution(
    monkeypatch: pytest.MonkeyPatch, demo_repo: tuple[Path, str, str]
) -> None:
    repo, base, head = demo_repo
    monkeypatch.setenv("VERIFIED_PR_AGENT_REPO_ROOT", str(repo))
    response = TestClient(app).post("/review", json={"base": base, "head": head})
    assert response.status_code == 200
    assert response.json()["test_results"] == []
    assert response.json()["findings"][0]["verification_status"] == "suspected"
