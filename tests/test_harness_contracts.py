"""Tests for SF-HARNESS-01: Shared test harness contracts and pytest adapter."""

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from selfforge_agent.models import TestResult
from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
    PytestSelection,
    TestCaseResult,
    TestExecutionRequest,
    TestPlan,
    UnsupportedBackendError,
)
from selfforge_agent.verification.harness.evaluator import evaluate_harness_result
from selfforge_agent.verification.harness.service import TestHarness
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner, TrustRequiredError


def create_fixture_repo(root: Path, test_content: str) -> Path:
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_sample.py").write_text(test_content, encoding="utf-8")
    return root


# ============================================================================
# SF-HARNESS-01-A1: Request/Plan contracts, validation & fail-closed precheck
# ============================================================================

def test_request_schema_validation_success(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    req = TestExecutionRequest(
        repository=repo,
        backend="pytest",
        cwd=".",
        selection=PytestSelection(test_path="tests", keyword=None),
        timeout=30.0,
        sandbox="host",
        trusted=True,
    )
    assert req.backend == "pytest"
    assert req.timeout == 30.0
    assert req.schema_version == "1.0"
    assert req.sandbox == "host"
    assert req.trusted is True


def test_request_fail_closed_on_unknown_fields(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    with pytest.raises(ValidationError):
        TestExecutionRequest(
            repository=repo,
            backend="pytest",
            unknown_arbitrary_field="malicious",
        )


def test_request_fail_closed_on_unsupported_version(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    with pytest.raises(ValidationError):
        TestExecutionRequest(
            repository=repo,
            backend="pytest",
            schema_version="99.0",
        )


def test_request_fail_closed_on_invalid_repository(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="repository must be an existing directory"):
        TestExecutionRequest(repository=non_existent, backend="pytest", trusted=True)

    with pytest.raises(ValueError, match="null bytes"):
        TestExecutionRequest(repository=Path("/tmp/foo\x00bar"), backend="pytest", trusted=True)


def test_request_fail_closed_on_traversal_cwd(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    with pytest.raises(ValueError, match="cwd traverses protected or invalid path component"):
        TestExecutionRequest(
            repository=repo,
            backend="pytest",
            cwd="../outside",
            trusted=True,
        )

    with pytest.raises(ValueError, match="cwd traverses protected or invalid path component"):
        TestExecutionRequest(
            repository=repo,
            backend="pytest",
            cwd=".git",
            trusted=True,
        )


def test_request_fail_closed_on_unsupported_backend(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="vitest",  # Unsupported until SF-HARNESS-03
        sandbox="host",
        trusted=True,
    )
    with patch("subprocess.Popen") as mock_popen:
        with pytest.raises(UnsupportedBackendError, match="Unsupported test backend 'vitest'"):
            harness.build_plan(req)
        mock_popen.assert_not_called()


def test_request_fail_closed_on_invalid_budget(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    for bad_timeout in [0, -10.0, 3601.0, float("nan"), float("inf"), True]:
        with pytest.raises((ValueError, TypeError, ValidationError)):
            TestExecutionRequest(
                repository=repo,
                backend="pytest",
                timeout=bad_timeout,
                trusted=True,
            )


def test_request_fail_closed_on_untrusted_host(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="pytest",
        sandbox="host",
        trusted=False,
    )
    with patch("subprocess.Popen") as mock_popen:
        with pytest.raises(TrustRequiredError):
            harness.build_plan(req)
        mock_popen.assert_not_called()


def test_pytest_selection_validation():
    # Leading hyphen in keyword
    with patch("subprocess.Popen") as mock_popen:
        with pytest.raises((ValueError, ValidationError), match="keyword must not start with '-'"):
            PytestSelection(keyword="-k foo")
        mock_popen.assert_not_called()

    # Traversal test_path
    with patch("subprocess.Popen") as mock_popen:
        with pytest.raises((ValueError, ValidationError), match="test_path traverses protected or invalid path component"):
            PytestSelection(test_path="../tests")
        mock_popen.assert_not_called()


def test_plan_deterministic_digest_and_immutability(tmp_path: Path):
    repo = create_fixture_repo(tmp_path, "def test_ok(): assert True\n")
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="pytest",
        cwd=".",
        selection=PytestSelection(test_path="tests", keyword=None),
        timeout=45.0,
        sandbox="host",
        trusted=True,
    )
    plan1 = harness.build_plan(req)
    plan2 = harness.build_plan(req)

    assert plan1.plan_digest == plan2.plan_digest
    assert len(plan1.plan_digest) == 64
    assert plan1.backend == "pytest"
    assert plan1.report_artifacts == ["junit.xml"]

    # Changing timeout or selection alters digest
    req_diff = req.model_copy(update={"timeout": 60.0})
    plan_diff = harness.build_plan(req_diff)
    assert plan_diff.plan_digest != plan1.plan_digest

    # Plan is immutable
    with pytest.raises((TypeError, ValidationError)):
        plan1.backend = "altered"  # type: ignore


# ============================================================================
# SF-HARNESS-01-A3: Shared success threshold & counter-examples
# ============================================================================

def make_sample_plan(tmp_path: Path) -> TestPlan:
    return TestPlan(
        schema_version="1.0",
        adapter_id="pytest",
        adapter_version="1.0",
        backend="pytest",
        repository=str(tmp_path),
        cwd=".",
        command=["python", "-m", "pytest", "tests"],
        environment="test-env",
        report_artifacts=["junit.xml"],
        selection_summary="tests",
        timeout=30.0,
        sandbox="host",
        plan_digest="0" * 64,
    )


def test_success_threshold_exit_zero_normal_passed(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=1.2,
        stdout="2 passed",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="2" failures="0" errors="0" skipped="0">
            <testcase classname="tests.test_sample" name="test_one" time="0.01"/>
            <testcase classname="tests.test_sample" name="test_two" time="0.01"/>
        </testsuite>""",
    )
    assert result.status == HarnessStatus.PASSED
    assert result.reason_code is None
    assert result.passed == 2
    assert result.failed == 0
    assert result.errors == 0
    assert result.skipped == 0
    assert result.total == 2
    assert result.is_success is True


def test_success_threshold_exit_zero_missing_report(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.5,
        stdout="pytest exited 0 but produced no junit report",
        stderr="",
        xml_bytes=None,
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.INVALID_EVIDENCE
    assert result.is_success is False
    assert "did not produce" in (result.error_message or "")


def test_success_threshold_exit_zero_empty_suite(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.5,
        stdout="collected 0 items",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="0" failures="0" errors="0" skipped="0">
        </testsuite>""",
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.EMPTY_SUITE
    assert result.is_success is False


def test_success_threshold_exit_zero_all_skipped(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.5,
        stdout="2 skipped",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="2" failures="0" errors="0" skipped="2">
            <testcase classname="tests.test_sample" name="test_s1"><skipped/></testcase>
            <testcase classname="tests.test_sample" name="test_s2"><skipped/></testcase>
        </testsuite>""",
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.ALL_SKIPPED
    assert result.is_success is False


def test_success_threshold_exit_zero_count_mismatch(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.5,
        stdout="inconsistent",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="5" failures="0" errors="0" skipped="0">
            <testcase classname="tests.test_sample" name="test_one"/>
        </testsuite>""",
    )
    # The xml specifies tests=5, failures=0, errors=0, skipped=0 -> computed passed=5, but only 1 testcase.
    # JUnit parser marks this as invalid or evaluate_harness_result catches count mismatch.
    assert result.status == HarnessStatus.ERROR
    assert result.is_success is False


def test_success_threshold_malformed_xml(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.5,
        stdout="",
        stderr="",
        xml_bytes=b"<truncated-xml-not-closed",
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.INVALID_EVIDENCE
    assert result.is_success is False


def test_success_threshold_test_failure(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=1,
        timed_out=False,
        cancelled=False,
        duration_seconds=1.0,
        stdout="1 failed, 1 passed",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
            <testcase classname="tests.test_sample" name="test_fail">
                <failure message="assert False">assert False</failure>
            </testcase>
            <testcase classname="tests.test_sample" name="test_pass"/>
        </testsuite>""",
    )
    assert result.status == HarnessStatus.FAILED
    assert result.reason_code == HarnessReasonCode.TEST_FAILURE
    assert result.is_success is False
    assert result.failed == 1
    assert result.passed == 1
    assert len(result.failure_details) == 1


def test_success_threshold_build_or_collection_error(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=2,  # Pytest exit 2 = Interrupted or collection failure
        timed_out=False,
        cancelled=False,
        duration_seconds=0.4,
        stdout="SyntaxError: invalid syntax",
        stderr="",
        xml_bytes=b"""<?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="0" failures="0" errors="1" skipped="0">
        </testsuite>""",
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.BUILD_ERROR
    assert result.is_success is False


# ============================================================================
# SF-HARNESS-01-A4: Timeout, cancellation, bounded diagnostics & failure classifications
# ============================================================================

def test_failure_classification_timeout(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=None,
        timed_out=True,
        cancelled=False,
        duration_seconds=30.1,
        stdout="test timed out",
        stderr="",
        xml_bytes=None,
    )
    assert result.status == HarnessStatus.TIMEOUT
    assert result.reason_code == HarnessReasonCode.TIMEOUT
    assert result.is_success is False
    assert "exceeded" in (result.error_message or "")


def test_failure_classification_cancellation(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    result = evaluate_harness_result(
        plan=plan,
        exit_code=None,
        timed_out=False,
        cancelled=True,
        duration_seconds=5.0,
        stdout="operation cancelled",
        stderr="",
        xml_bytes=None,
    )
    assert result.status == HarnessStatus.CANCELLED
    assert result.reason_code == HarnessReasonCode.CANCELLED
    assert result.is_success is False


def test_oversized_report_rejection(tmp_path: Path):
    plan = make_sample_plan(tmp_path)
    giant_xml = b"<testsuite>" + (b" " * (8 * 1024 * 1024 + 10)) + b"</testsuite>"
    result = evaluate_harness_result(
        plan=plan,
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_seconds=1.0,
        stdout="",
        stderr="",
        xml_bytes=giant_xml,
    )
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.INVALID_EVIDENCE
    assert result.is_success is False


def test_non_finite_metrics_rejected(tmp_path: Path):
    with pytest.raises((ValueError, ValidationError)):
        TestCaseResult(
            name="test_foo",
            classname="test_mod",
            status="passed",
            duration_seconds=float("nan"),
        )

    with pytest.raises((ValueError, ValidationError)):
        TestCaseResult(
            name="test_foo",
            classname="test_mod",
            status="passed",
            duration_seconds=float("inf"),
        )


# ============================================================================
# SF-HARNESS-01-A2: PytestAdapter, TestRunner & DockerTestRunner compatibility
# ============================================================================

def test_pytest_adapter_host_execution(tmp_path: Path):
    repo = create_fixture_repo(
        tmp_path,
        "def test_passing():\n    assert 1 + 1 == 2\n",
    )
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=repo,
        backend="pytest",
        cwd=".",
        selection=PytestSelection(test_path="tests"),
        timeout=30.0,
        sandbox="host",
        trusted=True,
    )
    plan = harness.build_plan(req)
    result = harness.execute(plan)

    assert result.status == HarnessStatus.PASSED
    assert result.is_success is True
    assert result.passed == 1
    assert result.failed == 0
    assert result.reason_code is None

    # Model bridge: can convert to legacy TestResult
    legacy = result.to_test_result()
    assert isinstance(legacy, TestResult)
    assert legacy.status == "passed"
    assert legacy.passed == 1
    assert legacy.failed == 0
    assert legacy.exit_code == 0


def test_test_runner_backward_compatibility(tmp_path: Path):
    repo = create_fixture_repo(
        tmp_path,
        "def test_pass_legacy():\n    assert True\n",
    )
    runner = TestRunner()
    assert runner.requires_trust is True

    # Trust required
    with pytest.raises(TrustRequiredError):
        runner.run(repo, trusted=False)

    # Trusted run
    res = runner.run(repo, trusted=True, timeout=30.0, test_path="tests")
    assert isinstance(res, TestResult)
    assert res.status == "passed"
    assert res.passed == 1
    assert res.failed == 0
    assert res.exit_code == 0


def test_docker_test_runner_validation():
    with pytest.raises(ValueError, match="Invalid Docker image name"):
        DockerTestRunner("invalid;rm -rf /")

    runner = DockerTestRunner("selfforge-agent-sandbox:latest")
    assert runner.requires_trust is False
    assert runner.image == "selfforge-agent-sandbox:latest"
