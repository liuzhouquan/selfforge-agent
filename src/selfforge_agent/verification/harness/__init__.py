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
from selfforge_agent.verification.harness.discovery import (
    AmbiguousSelectionError,
    ProjectCandidate,
    discover_project_candidates,
    resolve_project_candidate,
)
from selfforge_agent.verification.harness.evaluator import evaluate_harness_result
from selfforge_agent.verification.harness.profile import (
    TestProfile,
    build_frozen_plan_from_profile,
    load_test_profile,
)
from selfforge_agent.verification.harness.pytest_adapter import PytestAdapter, TrustRequiredError
from selfforge_agent.verification.harness.service import TestHarness

__all__ = [
    "AmbiguousSelectionError",
    "BaseHarnessAdapter",
    "HarnessReasonCode",
    "HarnessStatus",
    "ProjectCandidate",
    "PytestAdapter",
    "PytestSelection",
    "TestCaseResult",
    "TestExecutionRequest",
    "TestHarness",
    "TestPlan",
    "TestProfile",
    "TestRunResult",
    "TrustRequiredError",
    "UnsupportedBackendError",
    "build_frozen_plan_from_profile",
    "discover_project_candidates",
    "evaluate_harness_result",
    "load_test_profile",
    "resolve_project_candidate",
]
