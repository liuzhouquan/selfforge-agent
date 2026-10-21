"""Docker evidence contract and optional real-daemon integration."""

import subprocess
from pathlib import Path

import pytest

from selfforge_agent.coding.agent import RepairAgent
from selfforge_agent.coding.models import CodeEdit, RepairObservation
from selfforge_agent.runtime.execution import CommandResult, DockerExecutor
from selfforge_agent.verification.runner import DockerTestRunner


def test_docker_runner_uses_junit_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "tests").mkdir()
    xml = b'<testsuite tests="2" failures="1" errors="0" skipped="0" />'
    observed: dict[str, object] = {}

    def fake_run(self: DockerExecutor, command: str, timeout: float, name: str):
        observed.update(command=command, timeout=timeout, name=name)
        return CommandResult(1, "one assertion failed"), xml

    monkeypatch.setattr(DockerExecutor, "run_with_artifact", fake_run)
    result = DockerTestRunner().run(tmp_path, timeout=9)
    assert result.status == "failed"
    assert (result.passed, result.failed, result.errors) == (1, 1, 0)
    assert result.environment.startswith("docker;")
    assert observed == {"command": "python -m pytest tests --junitxml=/artifacts/junit.xml",
                        "timeout": 9, "name": "junit.xml"}


def test_docker_runner_rejects_missing_junit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "tests").mkdir()
    original = DockerExecutor.run_with_artifact
    monkeypatch.setattr(DockerExecutor, "run_with_artifact",
                        lambda *_args: (CommandResult(0, "claimed success"), None))
    result = DockerTestRunner().run(tmp_path)
    assert result.status == "error"
    assert "JUnit XML" in result.error_message
    with pytest.raises(ValueError, match="plain filename"):
        original(DockerExecutor(tmp_path), "true", 5, "../junit.xml")


@pytest.mark.integration
def test_real_docker_repairs_in_isolated_worktrees(demo_repo: tuple[Path, str, str]) -> None:
    image = "selfforge-agent-sandbox:latest"
    for probe in (["docker", "info"], ["docker", "image", "inspect", image]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")

    class FixedModel:
        def propose(self, _observation: RepairObservation) -> CodeEdit:
            return CodeEdit(path="calculator.py", old_text="return a - b",
                            new_text="return a + b", rationale="Restore addition")

    repo, base, head = demo_repo
    (repo / ".env").write_text("PRIVATE_TEST_SECRET=hidden", encoding="utf-8")
    isolated = DockerExecutor(repo).run("test ! -e .env && test -f calculator.py", 10)
    assert isolated.returncode == 0, isolated.output
    network = DockerExecutor(repo).run(
        "python -c 'import socket; socket.create_connection((\"1.1.1.1\", 80), 1)'", 10)
    assert network.returncode != 0 and not network.timed_out
    report = RepairAgent(FixedModel(), runner=DockerTestRunner(image)).run(
        repo, base, head, "Repair add regression")
    assert report.status == "verified", report.errors
    assert [report.base_test.status, report.head_test.status,
            report.attempts[0].test_result.status] == ["passed", "failed", "passed"]
    assert report.head_test.failed >= 1
    assert report.base_test.environment.startswith("docker;")
    assert "return a + b" in report.verified_patch
    assert "return a - b" in (repo / "calculator.py").read_text()
