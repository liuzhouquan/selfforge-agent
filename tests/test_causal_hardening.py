"""Defect reproduction and secondary hardening tests for causal investigation (SF-CAUSAL-02).

Validates:
1. Input defense: empty/whitespace target file path and null bytes in test_code rejected.
2. Causal contract invariant: causally_linked must never be True (traceback matches are associations).
3. Traceback parsing hardening: single-quote File 'path.py' frames and pytest colon formats.
4. External virtualenv/site-packages frame isolation: library frames do not match workspace targets.
5. Secondary causal disattribution: pre-existing module-level failures in base_test are disattributed.
6. Generic stems expansion (models, config, init, settings) preventing spurious low confidence.
7. Observed locations deduplication: recursive tracebacks do not flood duplicate location entries.
"""

import pytest
from pydantic import ValidationError

from selfforge_agent.coding.causal_investigation import (
    CausalInvestigation,
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


class TestCausalInputDefenseSecondary:
    """SF-CAUSAL-02: Enhanced input defense on target and test_code."""

    def test_target_file_path_empty_or_whitespace_rejected(self) -> None:
        """Empty or whitespace-only target file path must raise ValueError fail-closed."""
        for empty in ["", "   ", "\t", "\n"]:
            target = RiskTarget.model_construct(
                file_path=empty,
                line_number=10,
                description="desc",
                evidence="ev",
            )
            with pytest.raises(ValueError, match="[Ii]nvalid target file path"):
                investigate_causality(target, _make_test_result())

    def test_test_code_rejects_null_bytes(self) -> None:
        """Null bytes in test_code must be rejected fail-closed with ValueError."""
        target = RiskTarget(file_path="src/app.py", line_number=10, description="desc", evidence="ev")
        with pytest.raises(ValueError, match="null byte"):
            investigate_causality(target, _make_test_result(), test_code="def test_foo():\0pass")

    def test_causal_investigation_model_rejects_causally_linked_true(self) -> None:
        """causally_linked must never be set to True (traceback matches are associations, not proofs)."""
        target = RiskTarget(file_path="src/app.py", line_number=10, description="desc", evidence="ev")
        with pytest.raises((ValueError, ValidationError), match="[Cc]ausality|[Cc]ausally_linked"):
            CausalInvestigation(
                target=target,
                causally_linked=True,
                trace_associated=True,
                confidence="high",
                explanation="valid explanation",
            )


class TestTracebackPatternHardening:
    """SF-CAUSAL-02: Traceback extraction supporting diverse formatting and isolating external frames."""

    def test_single_quote_traceback_frame_extracted(self) -> None:
        """Single-quoted File 'mod.py', line 15 must be recognized as valid traceback frame."""
        target = RiskTarget(file_path="src/calculator.py", line_number=15, description="desc", evidence="ev")
        single_quote_stdout = "Traceback:\n  File 'src/calculator.py', line 15, in calculate\n    assert False"
        head = _make_test_result(stdout=single_quote_stdout)
        inv = investigate_causality(target, head)
        assert inv.matched_file is True
        assert inv.matched_line is True
        assert inv.confidence == "high"
        assert inv.trace_associated is True

    def test_pytest_colon_in_function_format(self) -> None:
        """Pytest syntax 'src/calc.py:20: in test_run' must be parsed as a valid frame."""
        target = RiskTarget(file_path="src/calc.py", line_number=20, description="desc", evidence="ev")
        stdout = "Failure summary:\nsrc/calc.py:20: in test_run\n    assert 1 == 2"
        head = _make_test_result(stdout=stdout)
        inv = investigate_causality(target, head)
        assert inv.matched_file is True
        assert inv.matched_line is True
        assert inv.confidence == "high"

    def test_external_virtualenv_site_packages_not_matched_to_target(self) -> None:
        """External site-packages / virtualenv frames must not be falsely attributed to workspace targets."""
        target = RiskTarget(file_path="calculator.py", line_number=15, description="desc", evidence="ev")
        # Traceback passes through virtualenv library file with same name
        external_stdout = (
            'Traceback:\n'
            '  File "/home/user/.venv/lib/python3.12/site-packages/thirdparty/calculator.py", line 15, in helper\n'
            '    raise RuntimeError("external")'
        )
        head = _make_test_result(stdout=external_stdout)
        inv = investigate_causality(target, head)
        # Should not attribute external site-packages traceback to workspace target
        assert inv.matched_file is False
        assert inv.matched_line is False
        assert inv.confidence == "none"


class TestModuleLevelCausalDisattribution:
    """SF-CAUSAL-02: Disattribution of pre-existing module-level failures."""

    def test_base_failure_in_same_module_disattributes_module_match(self) -> None:
        """Pre-existing module failure in base_test must disattribute module-level match to confidence='none'."""
        target = RiskTarget(file_path="src/calculator.py", line_number=10, description="desc", evidence="ev")
        # Head test fails in same module at line 50 (module match, not direct line match)
        head_failure = ModelTestFailure(
            test_id="test_calc",
            kind="failure",
            message="ValueError",
            detail='Traceback:\n  File "src/calculator.py", line 50, in other_fn\n    raise ValueError',
        )
        # Base test was already failing in that same module at line 50!
        base_failure = ModelTestFailure(
            test_id="test_calc",
            kind="failure",
            message="ValueError",
            detail='Traceback:\n  File "src/calculator.py", line 50, in other_fn\n    raise ValueError',
        )
        head = _make_test_result(status="failed", failed=1, details=[head_failure])
        base = _make_test_result(status="failed", failed=1, details=[base_failure])

        inv = investigate_causality(target, head, base_test=base)
        assert inv.confidence == "none", "Pre-existing module-level failure must be disattributed"
        assert "base test" in inv.explanation.lower()
        assert inv.causally_linked is False


class TestGenericStemExpansionAndDeduplication:
    """SF-CAUSAL-02: Extended generic stem filtering and observed location deduplication."""

    def test_generic_stem_models_config_init_do_not_trigger_false_positive(self) -> None:
        """Common module names (models, config, init, settings) must not trigger low-confidence stem match."""
        for generic in ["models.py", "config.py", "settings.py", "__init__.py"]:
            target = RiskTarget(file_path=f"pkg/{generic}", line_number=5, description="desc", evidence="ev")
            head = _make_test_result(
                status="failed",
                failed=1,
                stderr="Traceback in test_runner:\nfrom app.models import User\nimport config\nAssertionError",
            )
            inv = investigate_causality(target, head)
            assert inv.confidence == "none", f"Generic stem '{generic}' triggered false positive low confidence"
            assert inv.matched_file is False

    def test_observed_locations_deduplicated_under_recursive_traceback(self) -> None:
        """Observed locations must contain distinct entries without duplicates from recursion loops."""
        target = RiskTarget(file_path="src/other.py", line_number=1, description="desc", evidence="ev")
        # Recursive loop repeatedly hitting same location 20 times
        recursive_stdout = "\n".join(
            f'File "src/module.py", line 42, in recurse_{i}' for i in range(20)
        )
        head = _make_test_result(stdout=recursive_stdout)
        inv = investigate_causality(target, head)
        # Must be deduplicated: "src/module.py:42" should appear at most once
        assert inv.observed_locations.count("src/module.py:42") == 1
