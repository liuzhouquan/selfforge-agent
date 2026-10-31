"""Tests for risk-to-test causal investigation."""

from selfforge_agent.coding.causal_investigation import (
    investigate_causality,
)
from selfforge_agent.coding.test_generation import RiskTarget
from selfforge_agent.models import TestFailure as ModelTestFailure
from selfforge_agent.models import TestResult as ModelTestResult


def _make_test_result(
    *,
    status: str = "failed",
    failed: int = 1,
    details: list[ModelTestFailure] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> ModelTestResult:
    return ModelTestResult(
        command=["pytest"],
        environment="test",
        exit_code=1 if status == "failed" else 0,
        duration_seconds=0.5,
        passed=0,
        failed=failed,
        errors=0,
        skipped=0,
        status=status,
        stdout=stdout,
        stderr=stderr,
        failure_details=details or [],
    )


def test_investigate_causality_direct_line_match() -> None:
    target = RiskTarget(
        file_path="calculator.py",
        line_number=2,
        description="Dynamic eval",
        evidence="eval('1 + 1')",
        category="dynamic-code",
    )
    failure = ModelTestFailure(
        test_id="test_regression",
        kind="failure",
        message="ZeroDivisionError",
        detail='Traceback:\n  File "calculator.py", line 2, in add\n    eval("1/0")',
    )
    result = _make_test_result(details=[failure])

    investigation = investigate_causality(target, result)
    assert investigation.causally_linked is True
    assert investigation.confidence == "high"
    assert investigation.matched_file is True
    assert investigation.matched_line is True
    assert "calculator.py" in investigation.observed_locations[0]


def test_investigate_causality_module_level_match() -> None:
    target = RiskTarget(
        file_path="calculator.py",
        line_number=2,
        description="Dynamic eval",
        evidence="eval('1 + 1')",
    )
    failure = ModelTestFailure(
        test_id="test_regression",
        kind="failure",
        message="ValueError",
        detail='Traceback:\n  File "calculator.py", line 42, in other_fn\n    raise ValueError',
    )
    result = _make_test_result(details=[failure])

    investigation = investigate_causality(target, result)
    assert investigation.causally_linked is True
    assert investigation.confidence == "medium"
    assert investigation.matched_file is True
    assert investigation.matched_line is False


def test_investigate_causality_unrelated_failure() -> None:
    target = RiskTarget(
        file_path="calculator.py",
        line_number=2,
        description="Dynamic eval",
        evidence="eval('1 + 1')",
    )
    failure = ModelTestFailure(
        test_id="test_other",
        kind="failure",
        message="AssertionError",
        detail='Traceback:\n  File "other.py", line 10, in foo\n    assert False',
    )
    result = _make_test_result(details=[failure])

    investigation = investigate_causality(target, result)
    assert investigation.causally_linked is False
    assert investigation.confidence == "none"
    assert investigation.matched_file is False


def test_investigate_causality_no_target_or_passing_test() -> None:
    investigation = investigate_causality(None, _make_test_result())
    assert investigation.causally_linked is False
    assert investigation.confidence == "none"

    target = RiskTarget(
        file_path="calculator.py",
        line_number=2,
        description="Dynamic eval",
        evidence="eval('1 + 1')",
    )
    passing_result = _make_test_result(status="passed", failed=0)
    investigation_passing = investigate_causality(target, passing_result)
    assert investigation_passing.causally_linked is False
    assert investigation_passing.confidence == "none"
