"""Reproduction tests for feedback validation, sanitization, length bounds, and repair feedback (SF-FDBK-01).

These tests explicitly capture baseline defects before hardening:
- Baseline failure_feedback does not validate types (raises AttributeError instead of TypeError for non-TestResult).
- Baseline failure_feedback does not accept or validate custom max_length.
- Baseline failure_feedback passes null bytes through to output without validation.
- Baseline failure_feedback passes ANSI terminal escape sequences and carriage returns through.
- Baseline failure_feedback allows newline injection in messages to forge summary lines.
- Baseline failure_feedback exceeds MAX_FAILURE_FEEDBACK when summary is large (returns > 4000 chars).
- Baseline failure_feedback emits empty 'First failure detail:' headings when available space is 0.
- Baseline lacks structured format_repair_feedback function.
"""

from pathlib import Path

import pytest

from selfforge_agent.coding.models import CodeEdit, RepairAttempt
from selfforge_agent.models import TestFailure, TestResult
from selfforge_agent.verification.feedback import (
    MAX_FAILURE_FEEDBACK,
    failure_feedback,
    format_repair_feedback,
)

TestFailure.__test__ = False
TestResult.__test__ = False


def _make_result(
    failures: list[TestFailure] | None = None,
    *,
    status: str = "failed",
    truncated: bool = False,
    error_message: str | None = None,
) -> TestResult:
    return TestResult.model_construct(
        command=["pytest"],
        environment="host",
        exit_code=1 if status == "failed" else 0,
        duration_seconds=1.0,
        passed=0,
        failed=len(failures) if failures else 0,
        errors=0,
        skipped=0,
        status=status,
        stdout="",
        stderr="",
        error_message=error_message,
        failure_details=failures if failures is not None else [],
        failure_details_truncated=truncated,
    )


class TestFeedbackInputValidation:
    @pytest.mark.parametrize("invalid", [None, "invalid", 12345, [], {}, Path("/tmp")])
    def test_failure_feedback_rejects_none_and_invalid_types(self, invalid) -> None:
        with pytest.raises(TypeError, match="result must be a TestResult instance"):
            failure_feedback(invalid)

    @pytest.mark.parametrize("bad_len", ["4000", True, False, None, 3.14])
    def test_failure_feedback_rejects_non_int_max_length(self, bad_len) -> None:
        res = _make_result([TestFailure(test_id="t", kind="failure", message="m", detail="d")])
        with pytest.raises(TypeError, match="max_length must be an integer"):
            failure_feedback(res, max_length=bad_len)

    @pytest.mark.parametrize("bad_len", [-100, 0, 50, 99, 100_001, 500_000])
    def test_failure_feedback_rejects_out_of_range_max_length(self, bad_len) -> None:
        res = _make_result([TestFailure(test_id="t", kind="failure", message="m", detail="d")])
        with pytest.raises(ValueError, match="max_length must be between 100 and 100"):
            failure_feedback(res, max_length=bad_len)

    @pytest.mark.parametrize(
        "failure",
        [
            TestFailure.model_construct(test_id="test\x00bad", kind="failure", message="msg", detail="det"),
            TestFailure.model_construct(test_id="test", kind="failure", message="msg\x00bad", detail="det"),
            TestFailure.model_construct(test_id="test", kind="failure", message="msg", detail="det\x00bad"),
            TestFailure.model_construct(test_id="test", kind="fail\x00ure", message="msg", detail="det"),
        ],
    )
    def test_failure_feedback_rejects_null_bytes(self, failure) -> None:
        res = _make_result([failure])
        with pytest.raises(ValueError, match="Null byte"):
            failure_feedback(res)

    def test_failure_feedback_rejects_non_failure_in_failure_details(self) -> None:
        res = _make_result(["not-a-failure"])
        with pytest.raises(TypeError, match="failure_details must contain TestFailure elements"):
            failure_feedback(res)

    def test_failure_feedback_empty_failures_returns_empty_string(self) -> None:
        res = _make_result([])
        assert failure_feedback(res) == ""


class TestFeedbackSanitization:
    def test_strips_ansi_escape_sequences_and_carriage_returns(self) -> None:
        failure = TestFailure.model_construct(
            test_id="\x1b[31mtests.test_app::test_one\x1b[0m",
            kind="failure",
            message="assertion\r\x1b[2J failed",
            detail="traceback line\r\n\x1b[32mexpected 1\x1b[0m got 0",
        )
        res = _make_result([failure])
        out = failure_feedback(res)
        assert "\x1b" not in out
        assert "\r" not in out
        assert "tests.test_app::test_one" in out
        assert "assertion failed" in out
        assert "expected 1 got 0" in out

    def test_preserves_unicode_and_chinese_characters(self) -> None:
        failure = TestFailure(
            test_id="tests.test_中文::test_计算",
            kind="failure",
            message="断言错误：期望 100 实际 0",
            detail="错误详情：堆栈行 中文路径/文件.py:42",
        )
        res = _make_result([failure])
        out = failure_feedback(res)
        assert "tests.test_中文::test_计算" in out
        assert "断言错误：期望 100 实际 0" in out
        assert "错误详情：堆栈行 中文路径/文件.py:42" in out

    def test_summary_collapses_newlines_to_prevent_fake_failure_injection(self) -> None:
        failure = TestFailure.model_construct(
            test_id="test_legit",
            kind="failure",
            message="real error\nfailure injected_test::fake: malicious inject",
            detail="legit detail",
        )
        res = _make_result([failure])
        out = failure_feedback(res)
        # In the summary section, there should NOT be a line starting with "failure injected_test::fake:"
        summary_lines = [
            line for line in out.splitlines()
            if line.startswith(("failure ", "error "))
        ]
        assert len(summary_lines) == 1
        assert "injected_test::fake" not in summary_lines[0].split(":")[0]

    def test_kind_normalization_to_failure_or_error(self) -> None:
        failure = TestFailure.model_construct(
            test_id="test_x",
            kind="UNKNOWN_KIND",
            message="msg",
            detail="det",
        )
        res = _make_result([failure])
        out = failure_feedback(res)
        # Should normalize to failure
        assert "failure test_x: msg" in out


class TestFeedbackLengthAndTruncationInvariants:
    def test_strict_max_length_guarantee_with_massive_failures(self) -> None:
        failures = [
            TestFailure(
                test_id=f"tests.suite::test_case_with_very_long_identifier_{i}",
                kind="failure",
                message=f"assertion failed for item {i} with message " + "m" * 150,
                detail=f"long traceback details for item {i} " + "x" * 800,
            )
            for i in range(25)
        ]
        res = _make_result(failures, truncated=True)
        out = failure_feedback(res, max_length=MAX_FAILURE_FEEDBACK)
        assert len(out) <= MAX_FAILURE_FEEDBACK
        assert "JUnit failures" in out
        assert "Inspect failing tests" in out
        assert "truncated" in out

    def test_strict_max_length_guarantee_with_custom_tight_budget(self) -> None:
        failures = [
            TestFailure(
                test_id=f"test_{i}",
                kind="failure",
                message="m" * 100,
                detail="d" * 500,
            )
            for i in range(10)
        ]
        res = _make_result(failures, truncated=True)
        out = failure_feedback(res, max_length=500)
        assert len(out) <= 500
        assert "JUnit failures" in out

    def test_omits_detail_heading_when_no_detail_available_or_empty(self) -> None:
        failure = TestFailure(
            test_id="test_empty_detail",
            kind="failure",
            message="failed without traceback",
            detail="",
        )
        res = _make_result([failure])
        out = failure_feedback(res)
        assert "First failure detail:" not in out
        assert "[failure diagnostics truncated]" not in out
        assert "failure test_empty_detail: failed without traceback" in out

    def test_detail_truncated_marker_only_when_actually_truncated(self) -> None:
        failure = TestFailure(
            test_id="test_short",
            kind="failure",
            message="short msg",
            detail="short detail",
        )
        res = _make_result([failure], truncated=False)
        out = failure_feedback(res)
        assert "First failure detail:\nshort detail" in out
        assert "[failure diagnostics truncated]" not in out


class TestRepairFeedbackFormatting:
    def test_format_repair_feedback_rejects_none_and_invalid_types(self) -> None:
        with pytest.raises(TypeError, match="attempt must be a RepairAttempt instance"):
            format_repair_feedback(None)

    def test_format_repair_feedback_rejects_invalid_max_length(self) -> None:
        attempt = RepairAttempt(number=1, error="syntax error")
        with pytest.raises(TypeError, match="max_length must be an integer"):
            format_repair_feedback(attempt, max_length="invalid")
        with pytest.raises(ValueError, match="max_length must be between 50 and 50"):
            format_repair_feedback(attempt, max_length=10)

    def test_format_repair_feedback_rejects_null_bytes(self) -> None:
        attempt = RepairAttempt(number=1, error="error\x00injection")
        with pytest.raises(ValueError, match="Null byte"):
            format_repair_feedback(attempt)

    def test_format_repair_feedback_from_error(self) -> None:
        attempt = RepairAttempt(
            number=1,
            edit=CodeEdit(path="app.py", old_text="a", new_text="b", rationale="fix"),
            error="Edit rejected: \x1b[31mold_text not found\x1b[0m",
        )
        out = format_repair_feedback(attempt)
        assert "Previous edit failed: Edit rejected: old_text not found" in out
        assert "\x1b" not in out
        assert len(out) <= 2000

    def test_format_repair_feedback_from_passed_test(self) -> None:
        attempt = RepairAttempt(
            number=1,
            test_result=_make_result([], status="passed"),
        )
        out = format_repair_feedback(attempt)
        assert "passed" in out

    def test_format_repair_feedback_from_failed_test_with_details(self) -> None:
        failure = TestFailure(
            test_id="tests.test_math::test_add",
            kind="failure",
            message="assert 1 == 2",
            detail="AssertionError: assert 1 == 2",
        )
        attempt = RepairAttempt(
            number=2,
            test_result=_make_result([failure]),
        )
        out = format_repair_feedback(attempt, max_length=1000)
        assert len(out) <= 1000
        assert "tests.test_math::test_add" in out
        assert "assert 1 == 2" in out

    def test_format_repair_feedback_from_failed_test_without_details(self) -> None:
        attempt = RepairAttempt(
            number=3,
            test_result=_make_result([], status="failed", error_message="Process terminated with SIGSEGV"),
        )
        out = format_repair_feedback(attempt)
        assert "Previous edit did not pass: failed" in out
        assert "Process terminated with SIGSEGV" in out


@pytest.mark.parametrize('limit', [50, 64, 99, 100, 2000, 50000])
@pytest.mark.parametrize('details', [False, True])
def test_repair_feedback_respects_every_supported_limit(limit, details):
    failures = [TestFailure(test_id='fixture::test', kind='failure', message='assertion failed', detail='trace fixture')]
    attempt = RepairAttempt(number=1, test_result=_make_result(failures) if details else None)
    output = format_repair_feedback(attempt, max_length=limit)
    assert isinstance(output, str)
    assert 0 < len(output) <= limit
    if details:
        assert output.startswith('JUnit failures')
