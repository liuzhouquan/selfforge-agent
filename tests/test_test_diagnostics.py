"""Failure evidence survives noisy output and supports an actual repair loop."""

import json
import os
import py_compile
from pathlib import Path

import pytest

from selfforge_agent.models import TestResult as Result
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import CommandResult, DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import MAX_TOOL_OUTPUT, ToolRegistry
from selfforge_agent.verification.runner import DockerTestRunner
from selfforge_agent.verification.runner import TestRunner as HostTestRunner


def suite(root: Path, *, setup_error: bool = False) -> None:
    (root / "app.py").write_text("def value():\n    return 0\n")
    (root / "tests").mkdir()
    (root / "tests/test_value.py").write_text(
        "from app import value\n"
        + ("import pytest\n@pytest.fixture(autouse=True)\n"
           "def broken_setup():\n    raise RuntimeError('setup unavailable')\n"
           if setup_error else "")
        + "\ndef test_value():\n    assert value() == 1\n")


def xml(*, tests: int = 1, failures: int = 1, errors: int = 0, cases: str = "") -> bytes:
    return (f'<testsuite tests="{tests}" failures="{failures}" errors="{errors}" skipped="0">'
            + cases + '</testsuite>').encode()


def docker_report(root: Path, monkeypatch, report: bytes, output: str = "") -> Result:
    monkeypatch.setattr(DockerExecutor, "run_with_artifact", lambda *_a, **_kw:
                        (CommandResult(1, output), report))
    return DockerTestRunner().run(root)


def test_host_assertion_and_setup_errors_have_structured_evidence(tmp_path: Path) -> None:
    suite(tmp_path)
    result = HostTestRunner().run(tmp_path, trusted=True)
    assert result.status == "failed" and result.failed == 1
    failure = result.failure_details[0]
    assert failure.kind == "failure" and failure.test_id.endswith("::test_value")
    assert "0 == 1" in failure.message and "test_value.py" in failure.detail
    assert Result.model_validate_json(result.model_dump_json()).failure_details == [failure]
    (tmp_path / "tests/test_value.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\n"
        "def setup():\n    raise RuntimeError('setup unavailable')\n"
        "def test_value():\n    assert True\n")
    error = HostTestRunner().run(tmp_path, trusted=True)
    assert error.errors == 1 and error.failure_details[0].kind == "error"
    assert "setup unavailable" in error.failure_details[0].message


def test_noisy_stdout_cannot_hide_junit_failure_from_model(tmp_path: Path, monkeypatch) -> None:
    suite(tmp_path)
    report = xml(cases='<testcase classname="tests.test_value" name="test_value">'
                 '<failure message="assert 0 == 1">tests/test_value.py:4: AssertionError</failure>'
                 '</testcase>')
    docker_report(tmp_path, monkeypatch, report, output="noise " * 10_000)
    outcome = ToolRegistry(tmp_path, DockerExecutor(tmp_path)).execute("run_tests", {})
    assert not outcome.ok
    assert "tests.test_value::test_value" in outcome.content[:1500]
    assert "assert 0 == 1" in outcome.content[:1500]
    assert outcome.content.index("assert 0 == 1") < outcome.content.index("noise")
    assert len(outcome.content) <= MAX_TOOL_OUTPUT + len("\n[output truncated]")


def test_failure_limits_keep_counts_and_disclose_truncation(tmp_path: Path, monkeypatch) -> None:
    suite(tmp_path)
    cases = ''.join(f'<testcase classname="suite" name="test_{i}">'
                    f'<failure message="{"m" * 600}">{"x" * 4000}</failure></testcase>'
                    for i in range(20))
    result = docker_report(tmp_path, monkeypatch, xml(tests=20, failures=20, cases=cases))
    assert result.failed == 20 and len(result.failure_details) == 5
    assert result.failure_details_truncated is True
    assert all(len(f.message) <= 256 and len(f.detail) <= 1200 for f in result.failure_details)
    content = ToolRegistry(tmp_path, DockerExecutor(tmp_path)).execute("run_tests", {}).content
    assert "truncated" in content and "suite::test_4" in content
    assert "suite::test_5" not in content


@pytest.mark.parametrize("report", [
    b'<!DOCTYPE testsuite [<!ENTITY x "unsafe">]><testsuite tests="1"/>',
    b'<testsuite tests="1"><testcase name="t"><failure/></testcase></testsuite>',
    b'not xml',
])
def test_invalid_or_inconsistent_report_cannot_supply_failure_evidence(
    tmp_path: Path, monkeypatch, report: bytes,
) -> None:
    suite(tmp_path)
    result = docker_report(tmp_path, monkeypatch, report)
    assert result.status == "error"
    assert result.failure_details == []


def test_diagnostics_strip_terminal_controls_but_preserve_unicode(tmp_path: Path, monkeypatch) -> None:
    suite(tmp_path)
    report = xml(cases='<testcase classname="suite" name="test_中文">'
                 '<failure message="中文&#13;error">&#27;[31munsafe</failure></testcase>')
    # XML forbids ESC itself: encoded ESC must be rejected, not echoed to a terminal.
    invalid = docker_report(tmp_path, monkeypatch, report)
    assert invalid.status == "error" and not invalid.failure_details
    report = xml(cases='<testcase classname="suite" name="test_中文">'
                 '<failure message="中文&#13;error">line&#13;detail</failure></testcase>')
    result = docker_report(tmp_path, monkeypatch, report)
    assert "中文" in result.failure_details[0].test_id
    assert "\r" not in result.failure_details[0].message
    assert "\r" not in result.failure_details[0].detail


def test_collection_error_is_actionable_and_success_clears_failures(tmp_path: Path) -> None:
    suite(tmp_path)
    (tmp_path / "tests/test_value.py").write_text("import module_that_does_not_exist_12345\n")
    result = HostTestRunner().run(tmp_path, trusted=True)
    assert result.status == "failed" and result.errors > 0
    assert result.failure_details[0].kind == "error"
    assert "module_that_does_not_exist_12345" in result.failure_details[0].detail
    (tmp_path / "tests/test_value.py").write_text("def test_ok():\n    assert True\n")
    passed = HostTestRunner().run(tmp_path, trusted=True)
    assert passed.status == "passed" and passed.failure_details == []
    assert not passed.failure_details_truncated


class RepairModel:
    def __init__(self):
        self.step = 0

    def complete(self, messages, _tools, _timeout):
        self.step += 1
        if self.step == 2:
            feedback = messages[-1]["content"]
            assert "test_value" in feedback and "assert 0 == 1" in feedback
        if self.step == 4:
            return ModelTurn({"role": "assistant", "content": "Fixed and verified."},
                             "stop", "fixture", "feedback", None)
        name, args = (("edit_file", {"path": "app.py", "old_text": "return 0",
                                    "new_text": "return 1"}) if self.step == 2
                      else ("run_tests", {"path": "tests"}))
        return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
            "id": str(self.step), "type": "function", "function": {
                "name": name, "arguments": json.dumps(args)},
        }]}, "tool_calls", "fixture", "feedback", None)


def test_failed_test_feedback_drives_edit_retest_without_changing_assertions(tmp_path: Path) -> None:
    suite(tmp_path)
    original = (tmp_path / "tests/test_value.py").read_bytes()
    session = SessionStore(tmp_path)
    result = CodingAgent(RepairModel(), ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         session, max_steps=4).run("Fix value without changing tests")
    assert result.status == "completed"
    assert (tmp_path / "tests/test_value.py").read_bytes() == original
    assert [e["ok"] for e in session.trace_events() if e["event"] == "tool"] == [False, True, True]
    assert "assert 0 == 1" not in session.trace_path.read_text()


@pytest.mark.integration
def test_real_docker_failure_feedback_then_verified_repair(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        pytest.skip("Docker is not installed")
    for command in (["docker", "info"], ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        if subprocess.run(command, capture_output=True, timeout=10, check=False).returncode:
            pytest.skip("Docker daemon and sandbox image are required")
    suite(tmp_path)
    session = SessionStore(tmp_path)
    result = CodingAgent(RepairModel(), ToolRegistry(tmp_path, DockerExecutor(tmp_path)),
                         session, max_steps=4).run("Fix value without changing tests")
    assert result.status == "completed"
    assert [e["ok"] for e in session.trace_events() if e["event"] == "tool"] == [False, True, True]


def test_host_retest_ignores_stale_source_bytecode(tmp_path: Path) -> None:
    suite(tmp_path)
    source = tmp_path / "app.py"
    stamp = 1_700_000_000
    os.utime(source, (stamp, stamp))
    first = HostTestRunner().run(tmp_path, trusted=True)
    assert first.failed == 1
    py_compile.compile(str(source), doraise=True)
    source.write_text("def value():\n    return 1\n")
    os.utime(source, (stamp, stamp))  # Same size and timestamp can validate stale .pyc.
    second = HostTestRunner().run(tmp_path, trusted=True)
    assert second.status == "passed" and second.passed == 1


def test_host_cli_prints_failed_case_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    from selfforge_agent.cli import main

    suite(tmp_path)
    monkeypatch.setattr("sys.argv", ["selfforge-agent", "test", "--repo", str(tmp_path),
                                     "--sandbox", "host", "--trust-local"])
    assert main() == 1
    output = capsys.readouterr().out
    assert "test_value" in output and "assert 0 == 1" in output


def test_oversized_and_nested_junit_reports_fail_explicitly() -> None:
    from selfforge_agent.verification.junit import MAX_JUNIT_BYTES, parse_junit

    too_large = parse_junit(b"x" * (MAX_JUNIT_BYTES + 1), False)
    assert "8 MiB" in too_large.error and too_large.failures == []
    nested = parse_junit(b'<testsuite tests="1"><testsuite tests="1"/></testsuite>', False)
    assert "Nested" in nested.error
