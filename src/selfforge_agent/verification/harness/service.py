"""Central test harness service coordinating adapters and plan execution."""

from selfforge_agent.verification.harness.base_adapter import BaseHarnessAdapter
from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
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

    def execute_with_availability_check(self, target: TestPlan | TestExecutionRequest) -> TestRunResult:
        """Execute with controlled environment unavailable classification."""
        backend = target.backend
        sandbox = target.sandbox

        if backend not in self._adapters:
            return TestRunResult(
                run_id=f"run-env-unavail-{backend}",
                adapter_id=backend,
                adapter_version="1.0",
                plan_digest="0" * 64,
                status=HarnessStatus.ERROR,
                reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                exit_code=None,
                duration_seconds=0.0,
                error_message=f"Environment unavailable for backend {backend!r}: toolchain/adapter is not installed",
                environment=f"{sandbox}; backend={backend}; status=unavailable",
                command=[backend, "test"],
            )

        try:
            return self.execute(target)
        except UnsupportedBackendError as exc:
            return TestRunResult(
                run_id=f"run-env-unavail-{backend}",
                adapter_id=backend,
                adapter_version="1.0",
                plan_digest="0" * 64,
                status=HarnessStatus.ERROR,
                reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                exit_code=None,
                duration_seconds=0.0,
                error_message=f"Environment unavailable: {exc}",
                environment=f"{sandbox}; backend={backend}; status=unavailable",
                command=[backend, "test"],
            )
