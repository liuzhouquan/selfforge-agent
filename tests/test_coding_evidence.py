"""Coding completion needs nonempty JUnit evidence, including after resume."""

import json
import subprocess
import threading
from pathlib import Path

import pytest

from selfforge_agent.runtime.agent import SYSTEM_PROMPT, CodingAgent
from selfforge_agent.runtime.execution import CommandResult, DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.verification.runner import DockerTestRunner
from selfforge_agent.verification.runner import TestRunner as HostTestRunner


class VerificationModel:
    def __init__(self, *, test: bool = True) -> None:
        self.test = test

    def complete(self, _messages, _tools, _timeout):
        if self.test:
            self.test = False
            return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
                "id": "check", "type": "function", "function": {
                    "name": "run_tests", "arguments": json.dumps({"path": "tests"}),
                },
            }]}, "tool_calls", "fixture", "scripted", None)
        return ModelTurn({"role": "assistant", "content": "Finished."},
                         "stop", "fixture", "scripted", None)


def suite(root: Path, source: str) -> None:
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_case.py").write_text(source)


@pytest.mark.parametrize("source,expected", [
    ("import pytest\ndef test_skip():\n    pytest.skip('not configured')\n", "unverified"),
    ("# no test functions\n", "unverified"),
    ("def test_fail():\n    assert False\n", "unverified"),
    ("raise RuntimeError('collection failed')\n", "unverified"),
    ("def test_pass():\n    assert True\n", "completed"),
    (("import pytest\ndef test_pass():\n    assert True\n"
      "def test_skip():\n    pytest.skip('optional')\n"), "completed"),
])
def test_host_completion_and_resume_require_passing_tests(
    tmp_path: Path, source: str, expected: str,
) -> None:
    suite(tmp_path, source)
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    result = CodingAgent(VerificationModel(), tools, session).run("Verify the change")
    assert result.status == expected
    outcome = next(m["content"] for m in session.load()[0] if m["role"] == "tool")
    assert "passed=" in outcome and "skipped=" in outcome
    resumed = CodingAgent(VerificationModel(test=False), tools,
                          SessionStore(tmp_path, session.session_id)).run(resume=True)
    assert resumed.status == expected


def test_host_runner_rejects_all_skipped_and_empty_selection(tmp_path: Path) -> None:
    suite(tmp_path, "import pytest\ndef test_skip():\n    pytest.skip('optional')\n")
    runner = HostTestRunner()
    result = runner.run(tmp_path, trusted=True)
    assert result.status == "error" and result.exit_code == 0
    assert result.passed == 0 and result.skipped == 1
    assert "no passing tests" in result.error_message
    empty = runner.run(tmp_path, trusted=True, keyword="no_such_test")
    assert empty.status == "error" and empty.exit_code == 5


def test_host_tool_timeout_does_not_establish_evidence(tmp_path: Path) -> None:
    suite(tmp_path, "import time\ndef test_slow():\n    time.sleep(10)\n")
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    result = tools.execute("run_tests", {"timeout": 0.2})
    assert not result.ok
    assert "status=timeout" in result.content


@pytest.mark.parametrize("report", [None, b"broken XML"])
def test_host_tool_rejects_missing_or_corrupt_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: bytes | None,
) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            self.xml = Path(next(arg.split("=", 1)[1] for arg in command
                                 if arg.startswith("--junitxml=")))

        def communicate(self, timeout):
            if report is not None:
                self.xml.write_bytes(report)
            return b"100 passed", b""

    monkeypatch.setattr("selfforge_agent.verification.runner.subprocess.Popen", Process)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    result = tools.execute("run_tests", {})
    assert not result.ok and "status=error" in result.content
    assert "JUnit XML" in result.content


def test_host_runner_interrupt_kills_and_reaps_test_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from selfforge_agent.verification import runner

    suite(tmp_path, "def test_pass():\n    assert True\n")
    killed = []
    calls = []

    class Process:
        pid = 12345

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return b"", b""

        def kill(self):
            killed.append(self.pid)

    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    monkeypatch.setattr(runner.os, "killpg", lambda pid, _signal: killed.append(pid))
    with pytest.raises(KeyboardInterrupt):
        HostTestRunner().run(tmp_path, trusted=True, timeout=7)
    assert killed == [12345] and calls == [7, None]


PASS_XML = b'<testsuite tests="1" failures="0" errors="0" skipped="0" />'


@pytest.mark.parametrize("xml,code,timed_out,expected", [
    (PASS_XML, 0, False, True),
    (None, 0, False, False),
    (b"not XML", 0, False, False),
    (b'<testsuite tests="0" />', 0, False, False),
    (b'<testsuite tests="1" skipped="1" />', 0, False, False),
    (b'<testsuite tests="2" failures="1" />', 0, False, False),
    (b'<testsuite tests="2" errors="1" />', 0, False, False),
    (b'<testsuite tests="2" failures="-1" />', 0, False, False),
    (b'<testsuite tests="1" failures="2" />', 0, False, False),
    (PASS_XML, 2, False, False),
    (PASS_XML, None, True, False),
])
def test_docker_tool_uses_artifact_and_preserves_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    xml: bytes | None, code: int | None, timed_out: bool, expected: bool,
) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")
    cancel = threading.Event()
    executor = DockerExecutor(tmp_path, "project-tests:fixed", cancel=cancel)
    observed = []

    def artifact(self, command, timeout, name):
        assert self is executor and self.cancel is cancel
        observed.append((command, timeout, name))
        return CommandResult(code, "stdout claims 100 tests passed", timed_out), xml

    monkeypatch.setattr(DockerExecutor, "run_with_artifact", artifact)
    monkeypatch.setattr(DockerExecutor, "run", lambda *_args:
                        CommandResult(code, "stdout claims 100 tests passed", timed_out))
    outcome = ToolRegistry(tmp_path, executor).execute(
        "run_tests", {"keyword": "test_pass", "timeout": 7})
    assert outcome.ok is expected
    assert len(observed) == 1
    assert "-k test_pass" in observed[0][0]
    assert observed[0][1:] == (7, "junit.xml")


def test_existing_docker_cancellation_reaches_junit_staging(tmp_path: Path) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")
    cancel = threading.Event()
    cancel.set()
    # Staging must observe the existing marker before attempting a Docker command.
    result = ToolRegistry(tmp_path, DockerExecutor(tmp_path, cancel=cancel)).execute("run_tests", {})
    assert not result.ok and "cancelled" in result.content


def test_legacy_exit_code_evidence_requires_retesting(tmp_path: Path) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")
    session = SessionStore(tmp_path)
    session.record_message({"role": "system", "content": SYSTEM_PROMPT})
    session.record_message({"role": "user", "content": "Verify the change"})
    with session.path.open("a") as handle:
        handle.write('{"type":"verification","passed":true}\n')
    assert not session.verification_passed()
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    result = CodingAgent(VerificationModel(test=False), tools, session).run(resume=True)
    assert result.status == "unverified"
    result = CodingAgent(VerificationModel(), tools, session).run(resume=True)
    assert result.status == "completed" and session.verification_passed()


@pytest.mark.integration
def test_real_docker_skipped_tests_do_not_complete_coding(tmp_path: Path) -> None:
    for probe in (["docker", "info"], ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    suite(tmp_path, "import pytest\ndef test_skip():\n    pytest.skip('not configured')\n")
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, DockerExecutor(tmp_path))
    result = CodingAgent(VerificationModel(), tools, session).run("Verify the change")
    assert result.status == "unverified"
    outcome = next(m["content"] for m in session.load()[0] if m["role"] == "tool")
    assert "passed=0" in outcome and "skipped=1" in outcome
    suite(tmp_path, "def test_pass():\n    assert True\n")
    result = CodingAgent(VerificationModel(), tools, session).run(resume=True)
    assert result.status == "completed"


def test_docker_runner_cannot_run_another_workspace(tmp_path: Path) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="workspace"):
        DockerTestRunner().run(tmp_path, executor=DockerExecutor(other))


def test_unsupported_executor_cannot_fall_back_to_exit_code(tmp_path: Path) -> None:
    suite(tmp_path, "def test_pass():\n    assert True\n")

    class ExitCodeOnlyExecutor:
        persists_writes = False

        def run(self, command, timeout):
            pytest.fail("Unsupported executor must not run tests without JUnit evidence")

    result = ToolRegistry(tmp_path, ExitCodeOnlyExecutor()).execute("run_tests", {})
    assert not result.ok and "does not support JUnit" in result.content
