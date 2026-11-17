"""Small objective coding task evaluation over generated, trusted fixtures."""

from __future__ import annotations

import json
import math
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ChatModel, ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.verification.runner import TestRunner

MAX_CODE_CHARS = 1024 * 1024  # 1 MiB limit for source/test code
MAX_NAME_CHARS = 128
MAX_TASK_CHARS = 64 * 1024

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class EvalCase:
    name: str
    source: str
    test: str
    task: str
    old_text: str
    new_text: str

    def __post_init__(self) -> None:
        for field_name, value in [
            ("name", self.name),
            ("source", self.source),
            ("test", self.test),
            ("task", self.task),
            ("old_text", self.old_text),
            ("new_text", self.new_text),
        ]:
            if not isinstance(value, str):
                raise TypeError(f"EvalCase {field_name} must be a string, got {type(value).__name__}")
            if "\x00" in value:
                raise ValueError(f"EvalCase {field_name} must not contain null bytes")

        clean_name = self.name.strip()
        if not clean_name:
            raise ValueError("EvalCase name must not be blank")
        if len(clean_name) > MAX_NAME_CHARS:
            raise ValueError(f"EvalCase name exceeds maximum length of {MAX_NAME_CHARS} chars")
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError(f"EvalCase name contains invalid characters: {clean_name!r}")
        if "/" in self.name or "\\" in self.name:
            raise ValueError(f"EvalCase name must not contain directory slashes: {self.name!r}")

        for field_name, value, max_len in [
            ("source", self.source, MAX_CODE_CHARS),
            ("test", self.test, MAX_CODE_CHARS),
            ("task", self.task, MAX_TASK_CHARS),
            ("old_text", self.old_text, MAX_CODE_CHARS),
            ("new_text", self.new_text, MAX_CODE_CHARS),
        ]:
            if not value.strip() and field_name != "new_text":
                raise ValueError(f"EvalCase {field_name} must not be blank")
            if len(value) > max_len:
                raise ValueError(f"EvalCase {field_name} exceeds maximum length of {max_len} characters")

        if self.old_text not in self.source:
            raise ValueError("EvalCase old_text must be present in source code")


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        "addition",
        "def add(a, b):\n    return a - b\n",
        "from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "Fix app.py so add(2, 3) returns 5. Run the tests.",
        "return a - b",
        "return a + b",
    ),
    EvalCase(
        "normalization",
        "def normalize_name(name):\n    return name.upper()\n",
        "from app import normalize_name\n\ndef test_name():\n"
        "    assert normalize_name('  Alice  ') == 'alice'\n",
        "Fix app.py so normalize_name strips surrounding spaces and lowercases. Run the tests.",
        "return name.upper()",
        "return name.strip().lower()",
    ),
)


class OfflineFixtureModel:
    """Deterministic protocol fixture; does not measure language-model ability."""

    def __init__(self, case: EvalCase) -> None:
        if not isinstance(case, EvalCase):
            raise TypeError(f"case must be an EvalCase instance, got {type(case).__name__}")
        self.case = case
        self.index = 0

    def complete(self, _messages: list[dict], _tools: list[dict], _timeout: float) -> ModelTurn:
        if not isinstance(_timeout, (int, float)) or isinstance(_timeout, bool):
            raise TypeError("timeout must be a number")
        if not math.isfinite(_timeout) or _timeout <= 0:
            raise ValueError(f"timeout must be a finite positive number, got {_timeout}")
        if not isinstance(_messages, list):
            raise TypeError("messages must be a list")
        if not isinstance(_tools, list):
            raise TypeError("tools must be a list")

        actions = [
            ("search_code", {"pattern": "def ", "glob": "app.py"}),
            ("read_file", {"path": "app.py"}),
            ("edit_file", {"path": "app.py", "old_text": self.case.old_text,
                           "new_text": self.case.new_text}),
            ("run_tests", {"path": "tests"}),
        ]
        self.index += 1
        if self.index <= len(actions):
            name, arguments = actions[self.index - 1]
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": f"fixture-{self.index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}
            return ModelTurn(message, "tool_calls", "offline-fixture", "scripted", None)
        return ModelTurn({"role": "assistant", "content": "Code edited and tests executed."},
                         "stop", "offline-fixture", "scripted", None)


@dataclass(frozen=True)
class EvalOutcome:
    case: str
    success: bool
    status: str
    changed: bool
    test_exit_code: int | None
    reason: str
    steps: int
    tools: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.case, str) or not self.case.strip():
            raise ValueError("EvalOutcome case must be a non-empty string")
        if not isinstance(self.success, bool):
            raise TypeError("EvalOutcome success must be a boolean")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("EvalOutcome status must be a non-empty string")
        if not isinstance(self.changed, bool):
            raise TypeError("EvalOutcome changed must be a boolean")
        if self.test_exit_code is not None and (
            not isinstance(self.test_exit_code, int) or isinstance(self.test_exit_code, bool)
        ):
            raise TypeError("EvalOutcome test_exit_code must be an integer or None")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("EvalOutcome reason must be a non-empty string")
        if not isinstance(self.steps, int) or isinstance(self.steps, bool):
            raise TypeError("EvalOutcome steps must be an integer")
        if self.steps < 0:
            raise ValueError(f"EvalOutcome steps must be non-negative, got {self.steps}")
        if not isinstance(self.tools, list):
            raise TypeError("EvalOutcome tools must be a list of strings")
        for tool in self.tools:
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("EvalOutcome tools elements must be non-empty strings")

        if self.success:
            if self.status != "completed":
                raise ValueError("Successful evaluation outcome requires completed status")
            if not self.changed:
                raise ValueError("Successful evaluation outcome requires changed source")
            if self.test_exit_code != 0:
                raise ValueError("Successful evaluation outcome requires test_exit_code == 0")


def evaluate(
    model_factory: Callable[[EvalCase], ChatModel],
    *,
    max_steps: int = 10,
    time_budget: float = 180.0,
    cases: Sequence[EvalCase] | None = None,
    test_timeout: float = 30.0,
) -> dict[str, Any]:
    if not callable(model_factory):
        raise TypeError(f"model_factory must be callable, got {type(model_factory).__name__}")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool):
        raise TypeError("max_steps must be an integer")
    if not (1 <= max_steps <= 100):
        raise ValueError(f"max_steps must be between 1 and 100, got {max_steps}")
    if not isinstance(time_budget, (int, float)) or isinstance(time_budget, bool):
        raise TypeError("time_budget must be a number")
    if not (0.1 <= time_budget <= 86_400.0) or not math.isfinite(time_budget):
        raise ValueError(f"time_budget must be a finite number between 0.1 and 86400, got {time_budget}")
    if not isinstance(test_timeout, (int, float)) or isinstance(test_timeout, bool):
        raise TypeError("test_timeout must be a number")
    if not (0.1 <= test_timeout <= 3600.0) or not math.isfinite(test_timeout):
        raise ValueError(f"test_timeout must be a finite number between 0.1 and 3600, got {test_timeout}")

    if cases is None:
        target_cases: Sequence[EvalCase] = CASES
    else:
        if not isinstance(cases, (list, tuple)):
            raise TypeError(f"cases must be a sequence of EvalCase instances, got {type(cases).__name__}")
        for idx, item in enumerate(cases):
            if not isinstance(item, EvalCase):
                raise TypeError(f"Case at index {idx} must be an EvalCase instance, got {type(item).__name__}")
        target_cases = tuple(cases)

    if not target_cases:
        return {
            "tasks": 0,
            "successes": 0,
            "success_rate": 0.0,
            "results": [],
        }

    outcomes: list[EvalOutcome] = []
    for case in target_cases:
        with tempfile.TemporaryDirectory(prefix="selfforge-eval-", ignore_cleanup_errors=True) as temp:
            root = Path(temp).resolve()
            source = root / "app.py"
            source.write_text(case.source, encoding="utf-8")
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_app.py").write_text(case.test, encoding="utf-8")

            executor = HostExecutor(root, trusted=True)
            session = SessionStore(root)
            tools = ToolRegistry(root, executor)

            try:
                model = model_factory(case)
            except (RuntimeError, TypeError, ValueError, OSError, TimeoutError, AttributeError, KeyError) as exc:
                outcomes.append(EvalOutcome(
                    case=case.name,
                    success=False,
                    status="error",
                    changed=False,
                    test_exit_code=None,
                    reason=f"model_factory_error:{type(exc).__name__}",
                    steps=0,
                    tools=[],
                ))
                continue

            steps = 0
            status = "unknown"
            agent_error: str | None = None
            try:
                agent = CodingAgent(
                    model,
                    tools,
                    session,
                    max_steps=max_steps,
                    time_budget=time_budget,
                )
                result = agent.run(case.task)
                steps = result.steps
                status = result.status
                agent_error = "agent_failed" if result.error else None
            except (RuntimeError, TypeError, ValueError, OSError, TimeoutError, AttributeError, KeyError) as exc:
                status = "error"
                agent_error = f"agent_error:{type(exc).__name__}"

            test_exit_code: int | None = None
            verified = False
            changed = False
            try:
                content = tools._read_source(source, MAX_CODE_CHARS * 4)
                if len(content) > MAX_CODE_CHARS:
                    raise ValueError("Source exceeds case limit")
                changed = content != case.source
                # This demo has one supplied source file. Rebuild its original
                # tests outside the Agent tree; do not carry over pytest plugins,
                # modified tests, configuration or generated bytecode.
                with tempfile.TemporaryDirectory(prefix="selfforge-eval-verify-", ignore_cleanup_errors=True) as check_dir:
                    check_root = Path(check_dir)
                    (check_root / "app.py").write_text(content, encoding="utf-8")
                    (check_root / "tests").mkdir()
                    (check_root / "tests" / "test_app.py").write_text(case.test, encoding="utf-8")
                    verification = TestRunner().run(check_root, trusted=True,
                                                    timeout=test_timeout, test_path="tests")
                test_exit_code = verification.exit_code
                verified = (verification.status == "passed" and test_exit_code == 0
                            and verification.passed > 0 and verification.failed == 0
                            and verification.errors == 0)
                if not verified:
                    agent_error = agent_error or "independent_verification_failed"
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                agent_error = agent_error or f"verification_error:{type(exc).__name__}"

            success = changed and status == "completed" and verified
            if success:
                reason = "passed"
            else:
                reason = agent_error or f"status={status}; changed={changed}; test_exit={test_exit_code}"

            used_tools: list[str] = []
            try:
                for event in session.trace_events():
                    if isinstance(event, dict) and event.get("event") == "tool":
                        name = event.get("name")
                        if isinstance(name, str) and name:
                            used_tools.append(name)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                pass

            outcomes.append(EvalOutcome(
                case=case.name,
                success=success,
                status=status,
                changed=changed,
                test_exit_code=test_exit_code,
                reason=reason,
                steps=max(0, steps),
                tools=used_tools,
            ))

    successes = sum(1 for item in outcomes if item.success)
    total_tasks = len(outcomes)
    rate = (successes / total_tasks) if total_tasks > 0 else 0.0
    return {
        "tasks": total_tasks,
        "successes": successes,
        "success_rate": round(rate, 4),
        "results": [asdict(item) for item in outcomes],
    }
