"""Tests for JavaScript / TypeScript Vitest test harness backend (SF-HARNESS-03).

Covers:
- SF-HARNESS-03-A1: Single-shot execution, explicit selection, no watch mode, no npx download
- SF-HARNESS-03-A2: JUnit XML artifact isolation, unpolluted by stdout logs, lifecycle & error handling
- SF-HARNESS-03-A3: Build/transpile/import/config error categorization (not test_failure), timeout/cancel
- SF-HARNESS-03-A4: Shared threshold compliance, to_test_result() bridge, zero regression on pytest
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import CommandResult
from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
    TestExecutionRequest,
)
from selfforge_agent.verification.harness.service import TestHarness
from selfforge_agent.verification.harness.vitest_adapter import (
    VitestAdapter,
    VitestSelection,
)
from selfforge_agent.verification.runner import TrustRequiredError


def create_vitest_repo(root: Path, is_ts: bool = True) -> Path:
    """Create a minimal valid JS/TS repository with package.json and a test file."""
    repo = root / "vitest_repo"
    repo.mkdir(parents=True, exist_ok=True)
    pkg = {
        "name": "sample-project",
        "version": "1.0.0",
        "devDependencies": {
            "vitest": "^1.6.0",
        },
        "scripts": {
            "test": "vitest run",
        },
    }
    (repo / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")

    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)

    if is_ts:
        (tests_dir / "math.test.ts").write_text(
            'import { describe, it, expect } from "vitest";\n'
            'describe("math", () => {\n'
            '  it("adds numbers", () => {\n'
            "    expect(1 + 1).toBe(2);\n"
            "  });\n"
            "});\n",
            encoding="utf-8",
        )
    else:
        (tests_dir / "math.test.js").write_text(
            'import { describe, it, expect } from "vitest";\n'
            'describe("math", () => {\n'
            '  it("adds numbers", () => {\n'
            "    expect(1 + 1).toBe(2);\n"
            "  });\n"
            "});\n",
            encoding="utf-8",
        )
    return repo


# ============================================================================
# SF-HARNESS-03-A1: Single-shot execution, explicit selection, no watch / no npx download
# ============================================================================

def test_vitest_selection_validation_js_and_ts(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()

    # Positive control: TS file
    sel_ts = VitestSelection(test_path="tests/math.test.ts")
    req_ts = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection=sel_ts,
        sandbox="host",
        trusted=True,
    )
    adapter.validate_request(req_ts)

    # Positive control: JS file
    (repo / "tests" / "sample.test.js").write_text("console.log(1);", encoding="utf-8")
    sel_js = VitestSelection(test_path="tests/sample.test.js")
    req_js = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection=sel_js,
        sandbox="host",
        trusted=True,
    )
    adapter.validate_request(req_js)

    # Negative control: non-JS/TS test file rejected
    (repo / "tests" / "test_fake.py").write_text("def test_x(): pass", encoding="utf-8")
    sel_bad = VitestSelection(test_path="tests/test_fake.py")
    req_bad = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection=sel_bad,
        sandbox="host",
        trusted=True,
    )
    with pytest.raises(ValueError, match="not a JavaScript/TypeScript test file"):
        adapter.validate_request(req_bad)


def test_vitest_selection_pattern_and_traversal_guards(tmp_path: Path):
    # Invalid test_name_pattern starting with -
    with pytest.raises((ValueError, ValidationError)):
        VitestSelection(test_name_pattern="-invalid-flag")

    # Invalid test_name_pattern containing control characters
    with pytest.raises((ValueError, ValidationError)):
        VitestSelection(test_name_pattern="test\nname")

    # Protected path traversal in test_path
    with pytest.raises((ValueError, ValidationError)):
        VitestSelection(test_path=".git/hooks")

    with pytest.raises((ValueError, ValidationError)):
        VitestSelection(test_path="../outside.test.ts")


def test_vitest_plan_generation_single_shot_and_no_watch(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    req = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection=VitestSelection(test_path="tests/math.test.ts", test_name_pattern="adds"),
        sandbox="docker",
        image="selfforge-agent-sandbox:latest",
    )
    plan = adapter.create_plan(req)

    assert plan.backend == "vitest"
    assert plan.adapter_id == "vitest"
    # Must use single-shot 'run' command, never watch
    assert "run" in plan.command
    assert "watch" not in plan.command
    # Must explicitly configure junit reporter and artifact path
    assert "--reporter=junit" in plan.command
    assert "--outputFile=/artifacts/junit.xml" in plan.command
    assert "-t" in plan.command
    assert "adds" in plan.command
    assert plan.report_artifacts == ["junit.xml"]
    assert len(plan.plan_digest) == 64


def test_vitest_rejects_jest_and_browser_mode(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()

    # Rejection of Jest config file in selection
    (repo / "jest.config.js").write_text("module.exports = {};", encoding="utf-8")
    req_jest = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection={"config_file": "jest.config.js"},
        sandbox="host",
        trusted=True,
    )
    with pytest.raises((ValueError, ValidationError), match="Jest configuration is not supported"):
        adapter.validate_request(req_jest)

    # Rejection of browser mode in selection
    req_browser = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection={"browser": True},
        sandbox="host",
        trusted=True,
    )
    with pytest.raises((ValueError, ValidationError)):
        adapter.validate_request(req_browser)


def test_vitest_host_execution_requires_trust(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    req = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        sandbox="host",
        trusted=False,
    )
    with pytest.raises(TrustRequiredError, match="Pass trusted=True"):
        adapter.validate_request(req)


# ============================================================================
# SF-HARNESS-03-A2: JUnit XML artifact isolation, unpolluted by logs & lifecycle
# ============================================================================

SAMPLE_VITEST_JUNIT_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<testsuites name="vitest tests" tests="2" failures="0" errors="0" time="0.045">
    <testsuite name="tests/math.test.ts" timestamp="2026-11-20T12:00:00.000Z" hostname="selfforge" tests="2" failures="0" errors="0" time="0.045">
        <testcase classname="tests/math.test.ts" name="adds numbers" time="0.002">
        </testcase>
        <testcase classname="tests/math.test.ts" name="subtracts numbers" time="0.001">
        </testcase>
    </testsuite>
</testsuites>
"""

SAMPLE_VITEST_JUNIT_FAILING_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<testsuites name="vitest tests" tests="2" failures="1" errors="0" time="0.050">
    <testsuite name="tests/math.test.ts" timestamp="2026-11-20T12:00:00.000Z" hostname="selfforge" tests="2" failures="1" errors="0" time="0.050">
        <testcase classname="tests/math.test.ts" name="adds numbers" time="0.002">
        </testcase>
        <testcase classname="tests/math.test.ts" name="fails assertion" time="0.003">
            <failure message="expected 3 to be 4" type="AssertionError">
AssertionError: expected 3 to be 4
    at tests/math.test.ts:7:17
            </failure>
        </testcase>
    </testsuite>
</testsuites>
"""

SAMPLE_VITEST_JUNIT_ERROR_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<testsuites name="vitest tests" tests="1" failures="0" errors="1" time="0.020">
    <testsuite name="tests/syntax.test.ts" tests="1" failures="0" errors="1" time="0.020">
        <testcase classname="tests/syntax.test.ts" name="syntax error" time="0.001">
            <error message="Failed to parse source file" type="SyntaxError">
SyntaxError: Unexpected token
            </error>
        </testcase>
    </testsuite>
</testsuites>
"""


def test_vitest_docker_execution_success(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="vitest",
        selection=VitestSelection(test_path="tests/math.test.ts"),
        sandbox="docker",
    )
    plan = harness.build_plan(req)

    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(0, "stdout output with console logs"),
            SAMPLE_VITEST_JUNIT_XML,
        )
        mock_exec_cls.return_value = mock_instance

        result = harness.execute(plan)

        assert result.status == HarnessStatus.PASSED
        assert result.is_success is True
        assert result.passed == 2
        assert result.failed == 0
        assert result.errors == 0
        assert result.reason_code is None
        assert len(result.test_cases) == 2
        assert result.test_cases[0].name == "adds numbers"
        assert result.test_cases[0].status == "passed"


def test_vitest_stdout_logs_do_not_contaminate_xml(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        # Simulated stdout containing arbitrary logging, ANSI escape sequences and non-XML text
        mock_instance.run_with_artifact.return_value = (
            CommandResult(0, "\x1b[32mPASS\x1b[0m tests/math.test.ts\nSome debug print\n"),
            SAMPLE_VITEST_JUNIT_XML,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.is_success is True
        assert result.passed == 2
        assert "\x1b[32mPASS" in result.stdout


def test_vitest_missing_or_corrupt_artifact_fails_closed(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        # Exit 0 but missing artifact
        mock_instance.run_with_artifact.return_value = (
            CommandResult(0, "missing report"),
            None,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.ERROR
        assert result.reason_code == HarnessReasonCode.INVALID_EVIDENCE
        assert result.is_success is False


def test_vitest_empty_suite_and_all_skipped_fail(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    # Empty suite
    empty_xml = b"""<?xml version="1.0" encoding="UTF-8" ?>
    <testsuites name="vitest tests" tests="0" failures="0" errors="0" time="0.0">
        <testsuite name="empty" tests="0" failures="0" errors="0" time="0.0"/>
    </testsuites>"""
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (CommandResult(0, ""), empty_xml)
        mock_exec_cls.return_value = mock_instance

        res_empty = adapter.execute_plan(plan)
        assert res_empty.status == HarnessStatus.ERROR
        assert res_empty.reason_code == HarnessReasonCode.EMPTY_SUITE
        assert res_empty.is_success is False

    # All skipped
    all_skipped_xml = b"""<?xml version="1.0" encoding="UTF-8" ?>
    <testsuites name="vitest tests" tests="1" failures="0" errors="0" skipped="1" time="0.0">
        <testsuite name="skipped" tests="1" failures="0" errors="0" skipped="1" time="0.0">
            <testcase classname="math" name="skip me">
                <skipped message="todo"/>
            </testcase>
        </testsuite>
    </testsuites>"""
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (CommandResult(0, ""), all_skipped_xml)
        mock_exec_cls.return_value = mock_instance

        res_skipped = adapter.execute_plan(plan)
        assert res_skipped.status == HarnessStatus.ERROR
        assert res_skipped.reason_code == HarnessReasonCode.ALL_SKIPPED
        assert res_skipped.is_success is False


# ============================================================================
# SF-HARNESS-03-A3: Build/transpile/config error categorization & timeout/cancel
# ============================================================================

def test_vitest_assertion_failure_classified_as_test_failure(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(1, "1 failed"),
            SAMPLE_VITEST_JUNIT_FAILING_XML,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.FAILED
        assert result.reason_code == HarnessReasonCode.TEST_FAILURE
        assert result.failed == 1
        assert result.is_success is False


def test_vitest_transpile_and_syntax_error_classified_as_build_error(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    # Case A: Exit 1, no XML, transpile error in stderr
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(1, "SyntaxError: Unexpected token ':' in tests/math.test.ts"),
            None,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.ERROR
        assert result.reason_code == HarnessReasonCode.BUILD_ERROR
        assert result.is_success is False

    # Case B: Exit 1, XML contains errors=1 with 0 test failures
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(1, "Error during collection"),
            SAMPLE_VITEST_JUNIT_ERROR_XML,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.ERROR
        assert result.reason_code == HarnessReasonCode.BUILD_ERROR
        assert result.is_success is False


def test_vitest_timeout_and_cancellation(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    # Timeout
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(None, "Process timed out", timed_out=True),
            None,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.TIMEOUT
        assert result.reason_code == HarnessReasonCode.TIMEOUT
        assert result.passed == 0
        assert result.is_success is False

    # Cancellation
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(None, "Workspace staging cancelled"),
            None,
        )
        mock_exec_cls.return_value = mock_instance

        # Staging cancelled returns CommandResult(None, "Workspace staging cancelled")
        result = adapter.execute_plan(plan)
        assert result.status in (HarnessStatus.CANCELLED, HarnessStatus.ERROR)
        assert result.passed == 0
        assert result.is_success is False


def test_vitest_environment_unavailable_mapping(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    # In container: vitest command not found (exit 127)
    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(127, "sh: 1: vitest: not found"),
            None,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        assert result.status == HarnessStatus.ERROR
        assert result.reason_code == HarnessReasonCode.ENVIRONMENT_UNAVAILABLE
        assert result.is_success is False


# ============================================================================
# SF-HARNESS-03-A4: Shared threshold, to_test_result() bridge, python regression
# ============================================================================

def test_vitest_to_test_result_bridge(tmp_path: Path):
    repo = create_vitest_repo(tmp_path, is_ts=True)
    adapter = VitestAdapter()
    plan = adapter.create_plan(
        TestExecutionRequest(
            repository=repo,
            backend="vitest",
            sandbox="docker",
        )
    )

    with patch("selfforge_agent.verification.harness.vitest_adapter.DockerExecutor") as mock_exec_cls:
        mock_instance = MagicMock()
        mock_instance.run_with_artifact.return_value = (
            CommandResult(0, "All passed"),
            SAMPLE_VITEST_JUNIT_XML,
        )
        mock_exec_cls.return_value = mock_instance

        result = adapter.execute_plan(plan)
        legacy = result.to_test_result()

        assert isinstance(legacy, TestResult)
        assert legacy.status == "passed"
        assert legacy.passed == 2
        assert legacy.failed == 0
        assert legacy.exit_code == 0


def create_pytest_fixture_repo(root: Path, test_content: str) -> Path:
    repo = root / f"pytest_repo_{root.name}"
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_sample.py").write_text(test_content, encoding="utf-8")
    return repo


@pytest.mark.parametrize("backend", ["pytest", "vitest"])
def test_backend_parameterized_harness_integration(tmp_path: Path, backend: str):
    harness = TestHarness()
    if backend == "pytest":
        repo = create_pytest_fixture_repo(tmp_path, "def test_ok(): assert True\n")
        req = TestExecutionRequest(repository=repo, backend="pytest", sandbox="host", trusted=True)
        plan = harness.build_plan(req)
        assert plan.backend == "pytest"
        assert plan.adapter_id == "pytest"
    else:
        repo = create_vitest_repo(tmp_path, is_ts=True)
        req = TestExecutionRequest(repository=repo, backend="vitest", sandbox="docker")
        plan = harness.build_plan(req)
        assert plan.backend == "vitest"
        assert plan.adapter_id == "vitest"


def test_pytest_backward_compatibility_retained(tmp_path: Path):
    """Ensure pytest adapter has no behavioral regressions."""
    repo = create_pytest_fixture_repo(tmp_path, "def test_backward_compat(): assert 1 == 1\n")
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="pytest",
        sandbox="host",
        trusted=True,
    )
    plan = harness.build_plan(req)
    result = harness.execute(plan)

    assert result.status == HarnessStatus.PASSED
    assert result.is_success is True
    assert result.passed == 1
    assert result.failed == 0
