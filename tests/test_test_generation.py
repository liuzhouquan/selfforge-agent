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
from selfforge_agent.coding.test_generation import DifferentialTestGenerator, RiskTarget
from selfforge_agent.core.git_context import build_context
from selfforge_agent.integrations.github_pr import CheckoutResult
from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.verification.runner import TestRunner, TrustRequiredError


class TestModel:
    __test__ = False

    def __init__(self, code: str, *, model: str = "scripted", usage: TokenUsage | None = None) -> None:
        self.code = code
        self.model = model
        self.usage = usage

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
        }]}, "tool_calls", "fixture", self.model, self.usage)


class SequenceModel:
    def __init__(self, codes: list[str]) -> None:
        self.codes = codes.copy()
        self.prompts: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        self.prompts.append(json.loads(messages[1]["content"]))
        return TestModel(self.codes.pop(0)).complete(messages, tools, timeout)


def _risk() -> RiskTarget:
    return RiskTarget(file_path="calculator.py", line_number=2,
                      category="dynamic-code", description="Dynamic eval can execute input",
                      evidence="eval('1 + 1')")


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


def test_multiple_candidates_use_feedback_and_stop_on_differential(
    demo_repo: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repository, base, head = demo_repo
    model = SequenceModel([
        ("from calculator import add\n\ndef test_wrong_direction():\n"
         "    assert add(2, 3) == -1\n"),
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
        "def test_unused():\n    assert True\n",
    ])
    session = SessionStore(tmp_path / "sessions")
    batch = DifferentialTestGenerator(model, TestRunner(), session).run_many(
        repository, base, head, "Addition must still work", max_candidates=3,
        target=_risk(), trusted=True)
    assert batch.status == "differential" and batch.selected_candidate == 2
    assert batch.stop_reason == "differential"
    assert [item.status for item in batch.candidates] == ["inconclusive", "differential"]
    assert all(item.run_id == batch.run_id and item.target == _risk()
               for item in batch.candidates)
    assert model.prompts[0]["risk_hypothesis"]["file_path"] == "calculator.py"
    assert model.prompts[1]["previous_candidates"][0]["status"] == "inconclusive"
    assert model.prompts[1]["files"][0]["path"] == "calculator.py"
    assert len(model.codes) == 1
    assert not list(repository.glob("test_selfforge_*.py"))


def test_duplicate_candidate_is_not_run_twice(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    code = "def test_always_passes():\n    assert 1 + 1 == 2\n"
    model = SequenceModel([code, code])
    batch = DifferentialTestGenerator(model, TestRunner()).run_many(
        repository, base, head, "Check arithmetic", max_candidates=2, trusted=True)
    assert batch.status == "inconclusive" and batch.selected_candidate is None
    assert batch.stop_reason == "attempt_limit"
    assert batch.candidates[1].base_test is None and batch.candidates[1].head_test is None
    assert "repeated" in batch.candidates[1].errors[0]
    assert len({item.run_id for item in batch.candidates}) == 1


def test_risk_must_match_an_added_line(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    model = SequenceModel(["def test_unused():\n    assert True\n"])
    with pytest.raises(ValueError, match="added line"):
        DifferentialTestGenerator(model, TestRunner()).run_many(
            repository, base, head, "Check risk", target=_risk().model_copy(
                update={"evidence": "different code"}), trusted=True)
    assert not model.prompts


def test_multi_generation_enforces_total_time_budget(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    model = SequenceModel(["def test_unused():\n    assert True\n"])
    generator = DifferentialTestGenerator(model, TestRunner())
    with pytest.raises(ValueError, match="time_budget"):
        generator.run_many(repository, base, head, "Check behavior", trusted=True,
                           time_budget=0)
    report = generator.run(repository, base, head, "Check behavior", trusted=True,
                           deadline=-1)
    assert report.status == "error"
    assert "time budget exhausted" in report.errors[0]
    assert not model.prompts


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


def test_web_multi_generation_validates_risk_and_returns_candidates(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    url = "https://github.com/example/demo/pull/1"
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "checkouts")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-workspaces"))
    checkout_calls: list[str] = []
    monkeypatch.setattr(api_module, "load_pr", lambda _url: SimpleNamespace(
        url=url, context=build_context(repository, base, head)))

    def checkout(_url: str, _root: Path) -> CheckoutResult:
        checkout_calls.append(_url)
        return CheckoutResult(path=str(repository), branch="pr-1", head_sha=head,
                              diff_base_sha=base, pr_url=url)

    monkeypatch.setattr(api_module, "checkout_pr", checkout)
    model = SequenceModel([
        "def test_always_passes():\n    assert 1 + 1 == 2\n",
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
    ])
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: model)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    client = TestClient(api_module.app)
    payload = {"url": url, "task": "Check addition", "sandbox": "host",
               "trust_local_tests": True, "max_candidates": 3,
               "target": _risk().model_dump()}
    invalid = client.post("/api/github/generate-tests", json={
        **payload, "target": {**payload["target"], "line_number": 99}})
    assert invalid.status_code == 400 and not model.prompts and not checkout_calls
    response = client.post("/api/github/generate-tests", json=payload)
    assert response.status_code == 200, response.text
    batch = response.json()["batch"]
    assert batch["status"] == "differential"
    assert batch["selected_candidate"] == 2
    assert len(batch["candidates"]) == 2
    assert batch["target"]["evidence"] == _risk().evidence
    assert batch["candidates"][0]["base_test"]["passed"] == 1
    assert batch["candidates"][1]["head_test"]["failed"] == 1


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


@pytest.mark.integration
def test_multi_candidate_risk_flow_runs_in_real_docker(
    demo_repo: tuple[Path, str, str],
) -> None:
    for command in (["docker", "info"],
                    ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            probe = subprocess.run(command, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon or sandbox image unavailable")
        if probe.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    repository, base, head = demo_repo
    model = SequenceModel([
        "def test_always_passes():\n    assert 1 + 1 == 2\n",
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
    ])
    batch = DifferentialTestGenerator(model).run_many(
        repository, base, head, "Addition must work", target=_risk(),
        max_candidates=2)
    assert batch.status == "differential" and batch.selected_candidate == 2
    assert all(item.base_test.environment.startswith("docker;")
               for item in batch.candidates)
    assert batch.candidates[1].head_test.failed == 1


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


def test_cli_selects_baseline_finding_and_exports_batch(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    original_manager = CodingWorkspaceManager
    monkeypatch.setattr(cli, "CodingWorkspaceManager",
                        lambda: original_manager(tmp_path / "workspaces"))
    monkeypatch.setattr(cli, "route_providers", lambda _provider, _fallback: [object()])
    monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: SequenceModel([
        "def test_always_passes():\n    assert 1 + 1 == 2\n",
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
    ]))
    evidence, output = tmp_path / "batch.json", tmp_path / "test_regression.py"
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "generate-test",
                        "--repo", str(repository), "--base", base, "--head", head,
                        "--task", "Addition must work", "--sandbox", "host",
                        "--trust-local-tests", "--max-candidates", "2",
                        "--finding-index", "0", "--output", str(evidence),
                        "--test-output", str(output)])
    assert cli.main() == 0
    batch = json.loads(evidence.read_text())
    assert batch["target"]["file_path"] == "calculator.py"
    assert batch["selected_candidate"] == 2
    assert "assert add(2, 3) == 5" in output.read_text()


def test_differential_report_includes_causal_investigation_and_cost(
    demo_repo: tuple[Path, str, str], configured_rates: None,
) -> None:
    repository, base, head = demo_repo
    model = TestModel(
        "from calculator import add\n\ndef test_regression():\n"
        "    assert add(2, 3) == 5\n",
        model="deepseek-chat",
        usage=TokenUsage(prompt_tokens=1_000, completion_tokens=500, total_tokens=1_500),
    )
    generator = DifferentialTestGenerator(model, TestRunner())
    report = generator.run(
        repository, base, head, "Addition must work",
        target=_risk(), trusted=True,
    )
    assert report.status == "differential"
    assert report.cost is not None
    assert pytest.approx(report.cost.total_cost_usd, rel=1e-4) == 0.00028
    assert report.causal_investigation is not None
    assert report.causal_investigation.causally_linked is False
    assert report.causal_investigation.confidence in {"high", "medium", "low"}


def test_cli_auto_target_prioritizes_risk_and_exports_batch(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    original_manager = CodingWorkspaceManager
    monkeypatch.setattr(cli, "CodingWorkspaceManager",
                        lambda: original_manager(tmp_path / "workspaces"))
    monkeypatch.setattr(cli, "route_providers", lambda _provider, _fallback: [object()])
    monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: SequenceModel([
        "def test_always_passes():\n    assert 1 + 1 == 2\n",
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
    ]))
    evidence, output = tmp_path / "auto_batch.json", tmp_path / "test_regression.py"
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "generate-test",
                        "--repo", str(repository), "--base", base, "--head", head,
                        "--task", "Addition must work", "--sandbox", "host",
                        "--trust-local-tests", "--max-candidates", "2",
                        "--auto-target", "--output", str(evidence),
                        "--test-output", str(output)])
    assert cli.main() == 0
    batch = json.loads(evidence.read_text())
    assert batch["target"] is not None
    assert batch["target"]["file_path"] == "calculator.py"
    assert batch["selected_candidate"] == 2
    assert "assert add(2, 3) == 5" in output.read_text()


def test_web_prioritize_risks_and_auto_target_flow(
    demo_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base, head = demo_repo
    url = "https://github.com/example/demo/pull/1"
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "checkouts")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-workspaces"))
    monkeypatch.setattr(api_module, "load_pr", lambda _url: SimpleNamespace(
        url=url, context=build_context(repository, base, head)))
    monkeypatch.setattr(api_module, "checkout_pr", lambda _url, _root: CheckoutResult(
        path=str(repository), branch="pr-1", head_sha=head,
        diff_base_sha=base, pr_url=url))
    model = SequenceModel([
        "def test_always_passes():\n    assert 1 + 1 == 2\n",
        ("from calculator import add\n\ndef test_regression():\n"
         "    assert add(2, 3) == 5\n"),
    ])
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: model)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    client = TestClient(api_module.app)

    # 1. Test prioritize-risks endpoint
    prioritize_resp = client.post("/api/github/prioritize-risks", json={"url": url, "top_k": 3})
    assert prioritize_resp.status_code == 200, prioritize_resp.text
    risks = prioritize_resp.json()["risks"]
    assert len(risks) >= 1
    assert risks[0]["file_path"] == "calculator.py"
    assert risks[0]["rank"] == 1

    # 2. Test generate-tests with auto_target=True
    gen_resp = client.post("/api/github/generate-tests", json={
        "url": url, "task": "Check addition", "sandbox": "host",
        "trust_local_tests": True, "max_candidates": 3,
        "auto_target": True,
    })
    assert gen_resp.status_code == 200, gen_resp.text
    batch = gen_resp.json()["batch"]
    assert batch["status"] == "differential"
    assert batch["target"]["file_path"] == "calculator.py"
    assert batch["selected_candidate"] == 2
