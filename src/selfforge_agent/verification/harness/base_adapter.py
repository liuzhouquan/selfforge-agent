"""Base interface for language/framework test adapters."""

from abc import ABC, abstractmethod

from selfforge_agent.verification.harness.contracts import (
    TestExecutionRequest,
    TestPlan,
    TestRunResult,
)


class BaseHarnessAdapter(ABC):
    """Abstract base class for framework-specific harness adapters."""

    adapter_id: str
    adapter_version: str = "1.0"

    @abstractmethod
    def validate_request(self, request: TestExecutionRequest) -> None:
        """Validate framework-specific selection and preconditions.

        Must raise ValueError, ValidationError, or TrustRequiredError without
        starting any external processes.
        """

    @abstractmethod
    def create_plan(self, request: TestExecutionRequest) -> TestPlan:
        """Generate a deterministic, frozen test execution plan."""

    @abstractmethod
    def execute_plan(self, plan: TestPlan) -> TestRunResult:
        """Execute a frozen plan in the requested sandbox and return a normalized result."""
