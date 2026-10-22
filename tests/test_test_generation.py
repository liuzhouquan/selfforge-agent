"""A generated test is evidence only after Base/Head execution."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from selfforge_agent import cli
from selfforge_agent.api import app as api_module
from selfforge_agent.coding.test_generation import DifferentialTestGenerator
from selfforge_agent.integrations.github_pr import CheckoutResult
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.verification.runner import TestRunner, TrustRequiredError


class TestModel:
    __test__ = False

    def __init__(self, code: str) -> None:
        self.code = code

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        assert timeout > 0
        assert messages[1]["role"] == "user"
        assert tools[0]["function"]["name"] == "submit_test"
        return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
            "id": "candidate", "type": "function", "function": {
                "name": "submit_test", "arguments": json.dumps({
                    "test_code": self.code, "rationale": "Check addition",
                }),
            },
        }]}, "tool_calls", "fixture", "scripted", None)


def test_generated_test_proves_base_head_difference(
    demo_repo: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repository, base, head = demo_repo
    model = TestModel("from calculator import add\n\ndef test_regression():\n"
                      "    assert add(2, 3) == 5\n")
    session = SessionStore(tmp_path / "sessions")
    report = DifferentialTestGenerator(model, TestRunner(), session).run(
        repository, base, head, "Addition must work", trusted=True)
    assert report.status == "differential"
    assert report.base_test.status == "passed" and report.base_test.passed == 1
    assert report.head_test.status == "failed" and report.head_test.failed == 1
    assert report.head_test.errors == 0
    assert report.generated_test.test_code == model.code
    assert not (repository / report.test_path).exists()
    assert any(event["event"] == "differential_test" for event in session.trace_events())


def test_generated_test_is_inconclusive_when_both_versions_pass(
    demo_repo: tuple[Path, str, str],
) -> None:
    repository, base, head = demo_repo
    report = DifferentialTestGenerator(
        TestModel("def test_regression():\n    assert 1 + 1 == 2\n"), TestRunner(),
    ).run(repository, base, head, "Check behavior", trusted=True)
    assert report.status == "inconclusive"
    assert report.base_test.status == report.head_test.status == "passed"


def test_invalid_generated_test_is_not_executed(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    report = DifferentialTestGenerator(
        TestModel("def test_regression():\n    value = 1 + 1\n"), TestRunner(),
    ).run(repository, base, head, "Check behavior", trusted=True)
    assert report.status == "error"
    assert report.base_test is None and report.head_test is None
    assert "assert" in report.errors[0]


def test_host_generation_requires_explicit_trust(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    with pytest.raises(TrustRequiredError):
        DifferentialTestGenerator(TestModel("def test_x():\n    assert True\n"),
                                  TestRunner()).run(repository, base, head, "Check behavior")


def test_web_generation_requires_trust_and_returns_evidence(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    url = "https://github.com/example/demo/pull/1"
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "checkouts")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-workspaces"))
    monkeypatch.setattr(api_module, "load_pr", lambda _url: SimpleNamespace(
        url=url, context=SimpleNamespace(head_sha=head)))
    monkeypatch.setattr(api_module, "checkout_pr", lambda _url, _root: CheckoutResult(
        path=str(repository), branch="pr-1", head_sha=head,
        diff_base_sha=base, pr_url=url))
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: TestModel(
        "from calculator import add\n\ndef test_regression():\n"
        "    assert add(2, 3) == 5\n"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    client = TestClient(api_module.app)
    payload = {"url": url, "task": "Addition must work", "sandbox": "host",
               "trust_local_tests": True}
    assert client.post("/api/github/generate-test", json=payload).status_code == 403
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    response = client.post("/api/github/generate-test", json=payload)
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert report["status"] == "differential"
    assert report["base_test"]["passed"] == 1
    assert report["head_test"]["failed"] == 1
    assert not (repository / report["test_path"]).exists()


@pytest.mark.integration
def test_generated_test_runs_in_real_docker(demo_repo: tuple[Path, str, str]) -> None:
    for command in (["docker", "info"],
                    ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            probe = subprocess.run(command, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon or sandbox image unavailable")
        if probe.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    repository, base, head = demo_repo
    report = DifferentialTestGenerator(TestModel(
        "from calculator import add\n\ndef test_regression():\n"
        "    assert add(2, 3) == 5\n"),
    ).run(repository, base, head, "Addition must work")
    assert report.status == "differential", report.errors
    assert report.base_test.environment.startswith("docker;")
    assert report.head_test.failed == 1


def test_cli_exports_only_differential_test(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    original_manager = CodingWorkspaceManager
    monkeypatch.setattr(cli, "CodingWorkspaceManager",
                        lambda: original_manager(tmp_path / "workspaces"))
    monkeypatch.setattr(cli, "route_providers", lambda _provider, _fallback: [object()])
    monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: TestModel(
        "from calculator import add\n\ndef test_regression():\n"
        "    assert add(2, 3) == 5\n"))
    evidence, output = tmp_path / "evidence.json", tmp_path / "test_regression.py"
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "generate-test",
                        "--repo", str(repository), "--base", base, "--head", head,
                        "--task", "Addition must work", "--sandbox", "host",
                        "--trust-local-tests", "--output", str(evidence),
                        "--test-output", str(output)])
    assert cli.main() == 0
    assert json.loads(evidence.read_text())["status"] == "differential"
    assert "assert add(2, 3) == 5" in output.read_text()
