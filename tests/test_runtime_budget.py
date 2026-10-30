"""Budget feedback must help verification without manufacturing completion."""

import json
import threading
from pathlib import Path

import pytest

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.context import ContextManager
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry

PREFIX = "Runtime budget (generated for this turn):"


def call(name: str, arguments: dict, number: int) -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call-{number}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}, "tool_calls", "fixture", "budget", None)


def answer() -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": "Finished."},
                     "stop", "fixture", "budget", None)


class Model:
    def __init__(self, turns: list[ModelTurn]):
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self.schemas: list[list[dict]] = []

    def complete(self, messages, tools, timeout):
        assert timeout > 0
        self.requests.append(list(messages))
        self.schemas.append(tools)
        return next(self.turns)


def setup(root: Path) -> tuple[SessionStore, ToolRegistry]:
    (root / "app.py").write_text("def value():\n    return 0\n")
    (root / "tests").mkdir()
    (root / "tests/test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 1\n")
    return SessionStore(root), ToolRegistry(root, HostExecutor(root, trusted=True))


def notices(request: list[dict]) -> list[str]:
    return [message["content"] for message in request
            if message.get("role") == "system"
            and str(message.get("content", "")).startswith(PREFIX)]


def test_budget_feedback_tracks_edits_verification_and_final_turn(tmp_path: Path) -> None:
    session, tools = setup(tmp_path)
    model = Model([
        call("read_file", {"path": "app.py"}, 1),
        call("edit_file", {"path": "app.py", "old_text": "return 0",
                           "new_text": "return 1"}, 2),
        call("run_tests", {"path": "tests"}, 3), answer(),
    ])
    result = CodingAgent(model, tools, session, max_steps=4).run("Fix value; keep its API")
    assert result.status == "completed" and result.steps == 4
    for index, request in enumerate(model.requests, 1):
        current = notices(request)
        assert len(current) == 1
        assert f"Turn {index}/4" in current[0]
        assert request[1]["content"] == current[0]
        assert request[2] == {"role": "user", "content": "Fix value; keep its API"}
    assert "run_tests" in notices(model.requests[2])[0]
    assert "missing" in notices(model.requests[2])[0]
    assert "passed" in notices(model.requests[3])[0]
    assert "Final-answer-only" in notices(model.requests[3])[0]
    assert model.schemas[-1] == []
    assert not notices(session.load()[0])
    budgets = [event for event in session.trace_events() if event["event"] == "turn_budget"]
    assert [event["remaining_turns"] for event in budgets] == [4, 3, 2, 1]
    assert [event["verification_passed"] for event in budgets] == [False, False, False, True]
    assert budgets[-1]["final_only"] is True
    assert all("content" not in event for event in budgets)


def test_budget_feedback_never_promotes_bash_success_to_verification(tmp_path: Path) -> None:
    session, tools = setup(tmp_path)
    model = Model([call("bash", {"command": "true"}, index) for index in range(1, 4)])
    result = CodingAgent(model, tools, session, max_steps=3).run("Fix value")
    assert result.status == "max_steps" and result.steps == 3
    assert result.tools_used == 3 and len(model.requests) == 3
    assert all("missing" in notices(request)[0] for request in model.requests)
    assert "bash" in notices(model.requests[-1])[0]
    assert model.schemas[-1]  # No hidden extra finalization call or fabricated evidence.
    assert not session.verification_passed()


def test_budget_feedback_revokes_pass_after_another_edit(tmp_path: Path) -> None:
    session, tools = setup(tmp_path)
    model = Model([
        call("edit_file", {"path": "app.py", "old_text": "return 0",
                           "new_text": "return 1"}, 1),
        call("run_tests", {"path": "tests"}, 2),
        call("edit_file", {"path": "app.py", "old_text": "return 1",
                           "new_text": "return 0"}, 3), answer(),
    ])
    result = CodingAgent(model, tools, session, max_steps=4).run("Fix value")
    assert result.status == "unverified"
    assert "passed" in notices(model.requests[2])[0]
    assert "missing" in notices(model.requests[3])[0]
    assert "Final-answer-only" not in notices(model.requests[3])[0]
    assert not session.verification_passed()


def test_resume_regenerates_budget_without_stale_notices(tmp_path: Path) -> None:
    session, tools = setup(tmp_path)
    (tmp_path / "app.py").write_text("def value():\n    return 1\n")
    model = Model([call("run_tests", {"path": "tests"}, 1), answer()])
    first = CodingAgent(model, tools, session, max_steps=1).run("Check value")
    assert first.status == "max_steps"
    resumed = CodingAgent(model, tools, SessionStore(tmp_path, session.session_id),
                          max_steps=2).run(resume=True)
    assert resumed.status == "completed"
    assert "Turn 1/2" in notices(model.requests[-1])[0]
    assert "passed" in notices(model.requests[-1])[0]
    assert all(len(notices(request)) == 1 for request in model.requests)
    assert not notices(session.load()[0])


def test_compaction_reserves_room_for_current_budget_feedback(tmp_path: Path) -> None:
    session, tools = setup(tmp_path)
    (tmp_path / "large.py").write_text("# large tool output\n" * 300)
    model = Model([call("read_file", {"path": "large.py"}, 1),
                   call("read_file", {"path": "large.py"}, 2), answer()])
    result = CodingAgent(model, tools, session, max_steps=3, context_budget=700).run(
        "Inspect the file; do not change the public API")
    assert result.status == "unverified"
    assert all(ContextManager.estimate(request) <= 700 for request in model.requests)
    assert all(len(notices(request)) == 1 for request in model.requests)
    assert any(event["event"] == "context_compaction" for event in session.trace_events())
    assert not notices(session.load()[0])


@pytest.mark.parametrize("stop", ["cancelled", "timed_out"])
def test_stop_during_compaction_prevents_model_request(
    tmp_path: Path, monkeypatch, stop: str,
) -> None:
    session, tools = setup(tmp_path)
    cancelled = threading.Event()
    clock = [100.0]
    monkeypatch.setattr("selfforge_agent.runtime.agent.time.monotonic", lambda: clock[0])
    model = Model([])
    agent = CodingAgent(model, tools, session, cancel=cancelled, time_budget=10)
    original = agent.context.compact

    def compact(messages, **kwargs):
        result = original(messages, **kwargs)
        if stop == "cancelled":
            cancelled.set()
        else:
            clock[0] += 11
        return result

    monkeypatch.setattr(agent.context, "compact", compact)
    result = agent.run("Fix value")
    assert result.status == stop and result.steps == 0
    assert not model.requests
    assert not any(event["event"] == "turn_budget" for event in session.trace_events())
