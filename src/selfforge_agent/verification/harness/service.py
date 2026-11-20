"""Central test harness service coordinating adapters and plan execution."""

from selfforge_agent.verification.harness.base_adapter import BaseHarnessAdapter
from selfforge_agent.verification.harness.contracts import (
    TestExecutionRequest,
    TestPlan,
    TestRunResult,
    UnsupportedBackendError,
)
from selfforge_agent.verification.harness.pytest_adapter import PytestAdapter


class TestHarness:
    """Language-agnostic test harness coordinator."""

    __test__ = False

    def __init__(self, adapters: dict[str, BaseHarnessAdapter] | None = None) -> None:
        self._adapters: dict[str, BaseHarnessAdapter] = adapters if adapters is not None else {
            "pytest": PytestAdapter(),
        }

    def register_adapter(self, backend: str, adapter: BaseHarnessAdapter) -> None:
        """Register a new framework adapter."""
        self._adapters[backend] = adapter

    def get_adapter(self, backend: str) -> BaseHarnessAdapter:
        """Retrieve the adapter registered for a specific backend."""
        if backend not in self._adapters:
            raise UnsupportedBackendError(
                f"Unsupported test backend {backend!r}. "
                f"Available backends: {sorted(self._adapters.keys())}"
            )
        return self._adapters[backend]

    def build_plan(self, request: TestExecutionRequest) -> TestPlan:
        """Validate request and generate a frozen execution plan."""
        adapter = self.get_adapter(request.backend)
        return adapter.create_plan(request)

    def execute(self, target: TestPlan | TestExecutionRequest) -> TestRunResult:
        """Execute a plan or request through its registered adapter."""
        if isinstance(target, TestExecutionRequest):
            plan = self.build_plan(target)
        elif isinstance(target, TestPlan):
            plan = target
        else:
            raise TypeError(f"Expected TestPlan or TestExecutionRequest, got {type(target).__name__}")

        adapter = self.get_adapter(plan.backend)
        return adapter.execute_plan(plan)
