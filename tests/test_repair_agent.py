import json
import sys
from pathlib import Path

import pytest
from conftest import git

from verified_pr_agent.cli import main
from verified_pr_agent.coding.agent import RepairAgent
from verified_pr_agent.coding.model_client import (
    FallbackCodingModel,
    ModelError,
    OpenAICompatibleCodingModel,
)
from verified_pr_agent.coding.models import CodeEdit, RepairObservation, SourceFile
from verified_pr_agent.providers import ProviderConfig, TokenUsage
from verified_pr_agent.verification.runner import TrustRequiredError


class FixedModel:
    def __init__(self, path: str = "calculator.py") -> None:
        self.path = path
        self.calls = 0

    def propose(self, observation: RepairObservation) -> CodeEdit:
        self.calls += 1
        assert observation.attempt == self.calls
        assert "return a - b" in observation.files[0].content
        assert "FAILED" in observation.failing_test_output
        assert observation.findings[0].verification_status == "suspected"
        return CodeEdit(
            path=self.path,
            old_text="return a - b",
            new_text="return a + b",
            rationale="Restore addition",
        )


def test_verified_repair_keeps_original_tree_untouched(
    demo_repo: tuple[Path, str, str]
) -> None:
    repo, base, head = demo_repo
    before = git(repo, "worktree", "list", "--porcelain")
    report = RepairAgent(FixedModel()).run(
        repo, base, head, "Fix add regression", trusted=True
    )
    restored = type(report).model_validate_json(report.model_dump_json())
    assert restored.status == "verified"
    assert restored.base_test.status == "passed"
    assert restored.head_test.status == "failed"
    assert restored.findings[0].category == "dynamic-code"
    assert restored.attempts[0].test_result.status == "passed"
    assert "return a + b" in restored.verified_patch
    assert "tests/test_calculator.py" not in restored.verified_patch
    assert "return a - b" in (repo / "calculator.py").read_text()
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_rejects_test_edits(demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    report = RepairAgent(FixedModel(path="tests/test_calculator.py")).run(
        repo, base, head, "Fix add regression", trusted=True, max_attempts=1
    )
    assert report.status == "unresolved"
    assert report.verified_patch == ""
    assert report.attempts[0].test_result is None
    assert "allowed changed source" in report.attempts[0].error


def test_requires_trust_and_real_regression(demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    model = FixedModel()
    with pytest.raises(TrustRequiredError):
        RepairAgent(model).run(repo, base, head, "Fix add regression")
    report = RepairAgent(model).run(repo, base, base, "Fix add regression", trusted=True)
    assert report.status == "inconclusive"
    assert report.base_test.status == "passed"
    assert report.head_test.status == "passed"
    assert model.calls == 0


def test_retries_after_rejected_edit(demo_repo: tuple[Path, str, str]) -> None:
    class RetryModel:
        def propose(self, observation: RepairObservation) -> CodeEdit:
            if observation.attempt == 2:
                assert "not an allowed changed source file" in observation.feedback
            return CodeEdit(
                path="tests/test_calculator.py" if observation.attempt == 1 else "calculator.py",
                old_text="return a - b",
                new_text="return a + b",
                rationale="Fix the function",
            )

    repo, base, head = demo_repo
    report = RepairAgent(RetryModel()).run(repo, base, head, "Fix add", trusted=True)
    assert report.status == "verified"
    assert len(report.attempts) == 2
    assert report.attempts[0].error is not None
    assert report.attempts[1].test_result.status == "passed"


def test_model_client_parses_validated_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request_json(url: str, key: str, *, payload: dict, timeout: float) -> dict:
        assert timeout == 5
        assert key == "secret-for-test"
        assert url == "https://example.com/v1/chat/completions"
        assert "Fix x" in payload["messages"][1]["content"]
        content = {"path": "x.py", "old_text": "old", "new_text": "new", "rationale": "fix"}
        return {"choices": [{"message": {"content": json.dumps(content)}}]}

    monkeypatch.setattr("verified_pr_agent.coding.model_client.request_json", fake_request_json)
    client = OpenAICompatibleCodingModel(
        model="test-model", base_url="https://example.com/v1", api_key="secret-for-test", timeout=5
    )
    observation = RepairObservation(
        task="Fix x", attempt=1, files=[SourceFile(path="x.py", content="old")],
        failing_test_output="failed",
    )
    assert client.propose(observation).new_text == "new"
    with pytest.raises(ValueError):
        OpenAICompatibleCodingModel(model="m", base_url="http://example.com/v1", api_key="key")


def test_model_client_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "verified_pr_agent.coding.model_client.request_json",
        lambda *_a, **_k: {"choices": [{"message": {"content": "not json"}}]},
    )
    client = OpenAICompatibleCodingModel(model="m", base_url="http://localhost:1234/v1")
    observation = RepairObservation(task="Fix", attempt=1, files=[], failing_test_output="failed")
    with pytest.raises(ModelError):
        client.propose(observation)


def test_coding_model_switches_provider_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from verified_pr_agent.coding import model_client

    class FakeClient:
        def __init__(self, *, model: str, **_kwargs: object) -> None:
            self.model = model
            self.last_usage = TokenUsage(prompt_tokens=8, completion_tokens=2, total_tokens=10)

        def propose(self, _observation: RepairObservation) -> CodeEdit:
            if self.model == "first":
                raise ModelError("Provider response timed out")
            return CodeEdit(path="x.py", old_text="old", new_text="new", rationale="fix")

    monkeypatch.setattr(model_client, "OpenAICompatibleCodingModel", FakeClient)
    configs = [
        ProviderConfig(name="deepseek", label="DeepSeek", key="a", model="first",
                       base_url="https://api.deepseek.com", status_url=""),
        ProviderConfig(name="openrouter", label="OpenRouter", key="b", model="second",
                       base_url="https://openrouter.ai/api/v1", status_url=""),
    ]
    router = FallbackCodingModel(configs)
    observation = RepairObservation(task="Fix", attempt=1, files=[], failing_test_output="failed")
    assert router.propose(observation).new_text == "new"
    assert router.selected_provider == "openrouter"
    assert [attempt.status for attempt in router.attempts] == ["error", "success"]
    assert router.attempts[-1].usage.total_tokens == 10


def test_repair_cli_writes_evidence_and_verified_patch(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit = {
        "path": "calculator.py", "old_text": "return a - b",
        "new_text": "return a + b", "rationale": "Restore addition",
    }
    monkeypatch.setattr(
        "verified_pr_agent.coding.model_client.request_json",
        lambda *_a, **_k: {"choices": [{"message": {"content": json.dumps(edit)}}]},
    )
    monkeypatch.setenv("VPA_API_KEY", "test-key")
    repo, base, head = demo_repo
    output, patch = tmp_path / "report.json", tmp_path / "fix.patch"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verified-pr-agent", "repair", "--repo", str(repo), "--base", base,
            "--head", head, "--task", "Fix add", "--trust-local-tests", "--model", "fake",
            "--output", str(output), "--patch-output", str(patch),
        ],
    )
    assert main() == 0
    assert json.loads(output.read_text())["status"] == "verified"
    assert "return a + b" in patch.read_text()
