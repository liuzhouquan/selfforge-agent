"""Language-agnostic test harness architecture for SelfForge."""

from selfforge_agent.verification.harness.base_adapter import BaseHarnessAdapter
from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
    PytestSelection,
    TestCaseResult,
    TestExecutionRequest,
    TestPlan,
    TestRunResult,
    UnsupportedBackendError,
)
from selfforge_agent.verification.harness.evaluator import evaluate_harness_result
from selfforge_agent.verification.harness.pytest_adapter import PytestAdapter, TrustRequiredError
from selfforge_agent.verification.harness.service import TestHarness

__all__ = [
    "BaseHarnessAdapter",
    "HarnessReasonCode",
    "HarnessStatus",
    "PytestAdapter",
    "PytestSelection",
    "TestCaseResult",
    "TestExecutionRequest",
    "TestHarness",
    "TestPlan",
    "TestRunResult",
    "TrustRequiredError",
    "UnsupportedBackendError",
    "evaluate_harness_result",
]
