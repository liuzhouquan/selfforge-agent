"""Small objective coding task evaluation over generated, trusted fixtures."""

import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ChatModel, ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


@dataclass(frozen=True)
class EvalCase:
    name: str
    source: str
    test: str
    task: str
    old_text: str
    new_text: str


CASES = [
    EvalCase("addition", "def add(a, b):\n    return a - b\n",
             "from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
             "Fix app.py so add(2, 3) returns 5. Run the tests.",
             "return a - b", "return a + b"),
    EvalCase("normalization", "def normalize_name(name):\n    return name.upper()\n",
             "from app import normalize_name\n\ndef test_name():\n"
             "    assert normalize_name('  Alice  ') == 'alice'\n",
             "Fix app.py so normalize_name strips surrounding spaces and lowercases. Run the tests.",
             "return name.upper()", "return name.strip().lower()"),
]


class OfflineFixtureModel:
    """Deterministic protocol fixture; does not measure language-model ability."""

    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self.index = 0

    def complete(self, _messages: list[dict], _tools: list[dict], _timeout: float) -> ModelTurn:
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


def evaluate(model_factory: Callable[[EvalCase], ChatModel], *,
             max_steps: int = 10, time_budget: float = 180) -> dict:
    outcomes = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="selfforge-eval-") as temp:
            root = Path(temp)
            source = root / "app.py"
            source.write_text(case.source, encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text(case.test, encoding="utf-8")
            executor = HostExecutor(root, trusted=True)
            session = SessionStore(root)
            agent = CodingAgent(model_factory(case), ToolRegistry(root, executor),
                                session, max_steps=max_steps,
                                time_budget=time_budget)
            result = agent.run(case.task)
            test = executor.run("python -m pytest tests -q", 30)
            changed = source.read_text(encoding="utf-8") != case.source
            success = changed and result.status == "completed" and test.returncode == 0
            reason = "passed" if success else (
                result.error or f"status={result.status}; changed={changed}; test_exit={test.returncode}"
            )
            outcomes.append(EvalOutcome(case.name, success, result.status, changed,
                                        test.returncode, reason, result.steps,
                                        [event["name"] for event in session.trace_events()
                                         if event["event"] == "tool"]))
    successes = sum(item.success for item in outcomes)
    return {"tasks": len(outcomes), "successes": successes,
            "success_rate": successes / len(outcomes),
            "results": [asdict(item) for item in outcomes]}
