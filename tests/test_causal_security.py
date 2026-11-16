"""Security, input validation, and causal disattribution tests for causal investigation (SF-CAUSAL-01)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from selfforge_agent.coding.causal_investigation import (
    _normalize_path,
    _truncate_text,
    investigate_causality,
)
from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import TestFailure as ModelTestFailure
from selfforge_agent.models import TestResult as ModelTestResult


def _make_test_result(
    *,
    status: str = "failed",
    failed: int = 1,
    details: list | None = None,
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


class TestCausalInputValidationSecurity:
    """SF-CAUSAL-01-A1: Strict input gating and type safety."""

    def test_investigate_causality_rejects_invalid_target_type(self):
        for invalid in ["not_a_target", 12345, [1, 2, 3], {"file_path": "a.py", "line_number": 1}]:
            with pytest.raises(TypeError, match="[Tt]arget"):
                investigate_causality(invalid, _make_test_result())

    def test_investigate_causality_rejects_invalid_head_test_type(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        for invalid in ["not_a_result", 12345, [1, 2, 3], {"status": "failed"}]:
            with pytest.raises(TypeError, match="[Hh]ead_test"):
                investigate_causality(target, invalid)

    def test_investigate_causality_rejects_invalid_base_test_type(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        head = _make_test_result()
        for invalid in ["not_a_result", 12345, [1, 2, 3], {"status": "passed"}]:
            with pytest.raises(TypeError, match="[Bb]ase_test"):
                investigate_causality(target, head, base_test=invalid)

    def test_investigate_causality_rejects_invalid_test_code_type(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        head = _make_test_result()
        for invalid in [12345, [1, 2, 3], {"code": "pass"}, None]:
            with pytest.raises(TypeError, match="[Tt]est_code"):
                investigate_causality(target, head, test_code=invalid)

    def test_target_line_number_excessive_bounds(self):
        target = RiskTarget(file_path="mod.py", line_number=100_000_000, description="desc", evidence="ev")
        head = _make_test_result(stdout='File "mod.py", line 100000000, in func')
        inv = investigate_causality(target, head)
        assert inv.causally_linked is False
        assert inv.confidence == "none"
        assert "line number" in inv.explanation.lower()

    def test_target_file_path_invalid_or_null_bytes(self):
        target_bad_type = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        target_bad_type.file_path = "mod\0evil.py"
        head = _make_test_result()
        with pytest.raises(ValueError, match="null byte"):
            investigate_causality(target_bad_type, head)

    def test_truncate_text_rejects_invalid_types_and_bounds(self):
        for invalid in [12345, [1, 2], {"text": "hello"}]:
            with pytest.raises(TypeError, match="[Tt]ext"):
                _truncate_text(invalid)

        for invalid_limit in [0, -10, "100", 3.14, True]:
            with pytest.raises((ValueError, TypeError)):
                _truncate_text("valid text", max_bytes=invalid_limit)

    def test_truncate_text_strips_null_bytes(self):
        raw = "error message\0with null bytes"
        truncated = _truncate_text(raw, max_bytes=100)
        assert "\0" not in truncated
        assert "error message" in truncated


class TestPathNormalizationAndStackExtractionSecurity:
    """SF-CAUSAL-01-A2: Path normalization and bounded stack extraction."""

    def test_normalize_path_type_defense(self):
        for invalid in [None, 12345, Path("a/b"), ["path"]]:
            with pytest.raises(TypeError, match="[Pp]ath"):
                _normalize_path(invalid)

    def test_normalize_path_rejects_null_bytes(self):
        with pytest.raises(ValueError, match="null byte"):
            _normalize_path("path/\0evil.py")

    def test_normalize_path_canonicalizes_slashes_and_dots(self):
        assert _normalize_path("./a//b/c.py") == "a/b/c.py"
        assert _normalize_path("a\\b\\c.py") == "a/b/c.py"
        assert _normalize_path("  pkg/mod.py  ") == "pkg/mod.py"

    def test_traceback_regex_handles_extreme_digit_lengths_without_crash(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        huge_line = "9" * 5000
        head = _make_test_result(stdout=f'File "mod.py", line {huge_line}, in func')
        inv = investigate_causality(target, head)
        assert inv.causally_linked is False

    def test_stack_frames_bounded_extraction_under_huge_traceback(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        # Generate 1000 lines of traceback frames
        repeated_frames = "\n".join(f'File "frame_{i}.py", line {i}, in func_{i}' for i in range(1000))
        head = _make_test_result(stdout=repeated_frames)
        inv = investigate_causality(target, head)
        assert len(inv.observed_locations) <= 5


class TestBaseTestComparisonAndCausalDisattribution:
    """SF-CAUSAL-01-A3: Base test comparison and stem matching precision."""

    def test_pre_existing_base_failure_disattributes_causality(self):
        target = RiskTarget(file_path="calculator.py", line_number=2, description="eval", evidence="eval")
        failure_detail = 'Traceback:\n  File "calculator.py", line 2, in add\n    eval("1/0")'
        head_failure = ModelTestFailure(test_id="t1", kind="failure", message="ZeroDiv", detail=failure_detail)
        base_failure = ModelTestFailure(test_id="t1", kind="failure", message="ZeroDiv", detail=failure_detail)

        head = _make_test_result(status="failed", failed=1, details=[head_failure])
        base = _make_test_result(status="failed", failed=1, details=[base_failure])

        inv = investigate_causality(target, head, base_test=base)
        assert inv.causally_linked is False
        assert inv.confidence == "none"
        assert "base test" in inv.explanation.lower()

    def test_short_target_stem_does_not_trigger_substring_false_positive(self):
        # Target stem is 'io' (from io.py).
        target = RiskTarget(file_path="src/io.py", line_number=5, description="desc", evidence="ev")
        # Stderr contains words with 'io' like 'AssertionError' or 'calculation'
        head = _make_test_result(
            status="failed",
            failed=1,
            stderr="AssertionError: calculation failed during serialization\nFile 'other.py', line 50",
        )
        inv = investigate_causality(target, head)
        assert inv.confidence == "none"
        assert inv.matched_file is False
        assert inv.trace_associated is False

    def test_generic_stem_words_do_not_trigger_false_positive(self):
        for generic_name in ["test.py", "conftest.py", "utils.py"]:
            target = RiskTarget(file_path=f"pkg/{generic_name}", line_number=5, description="desc", evidence="ev")
            head = _make_test_result(
                status="failed",
                failed=1,
                stderr="pytest execution test run failed: assertion error in test runner utils",
            )
            inv = investigate_causality(target, head)
            # Should not jump to low confidence just because word 'test' or 'utils' appears in failure summary
            assert inv.confidence == "none"
            assert inv.matched_file is False


class TestFailureDetailsRobustnessAndContract:
    """SF-CAUSAL-01-A4: Failure details fault tolerance and output contract."""

    def test_head_test_with_none_or_malformed_failure_details(self):
        target = RiskTarget(file_path="calculator.py", line_number=2, description="eval", evidence="eval")
        # Use an object whose failure_details list contains malformed elements
        head = _make_test_result()
        # Directly inject malformed elements into failure_details list
        head.failure_details.append(SimpleNamespace(message=None, detail=None))
        head.failure_details.append(SimpleNamespace(invalid=True))
        head.failure_details.append(None)  # type: ignore
        inv = investigate_causality(target, head)
        assert inv.causally_linked is False
        assert inv.confidence == "none"

    def test_observed_locations_sanitizes_newlines_and_control_chars(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        head = _make_test_result(stdout='File "mod\n\r.py", line 10, in func')
        inv = investigate_causality(target, head)
        for loc in inv.observed_locations:
            assert "\n" not in loc
            assert "\r" not in loc

    def test_explanation_is_bounded_and_non_empty(self):
        target = RiskTarget(file_path="mod.py", line_number=10, description="desc", evidence="ev")
        head = _make_test_result()
        inv = investigate_causality(target, head)
        assert isinstance(inv.explanation, str)
        assert 0 < len(inv.explanation) <= 2048
        assert inv.causally_linked is False
