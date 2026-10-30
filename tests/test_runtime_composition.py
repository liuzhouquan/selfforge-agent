"""Exercise safety/evidence/environment/budget controls in one resumed task."""

import json
from pathlib import Path

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


def call(name: str, arguments: dict, number: int) -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call-{number}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)},
    }]}, "tool_calls", "fixture", "composition", None)


class Model:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []
        self.schemas = []

    def complete(self, messages, tools, _timeout):
        self.requests.append(list(messages))
        self.schemas.append(tools)
        return next(self.turns)


def test_interrupted_edit_revokes_junit_evidence_and_resume_retests(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "app.py").write_text("def value():\n    return 1\n")
    test = workspace / "tests/test_value.py"
    test.write_text("from app import value\ndef test_value():\n    assert value() == 1\n")
    original_tests = test.read_bytes()
    session = SessionStore(tmp_path / "state")

    class InterruptedTools(ToolRegistry):
        def execute(self, name, arguments):
            outcome = super().execute(name, arguments)
            if name == "edit_file":
                assert outcome.ok
                raise KeyboardInterrupt  # The write happened, but no tool reply was persisted.
            return outcome

    first = Model([
        call("run_tests", {"path": "tests"}, 1),
        call("edit_file", {"path": "app.py", "old_text": "return 1", "new_text": "return 0"}, 2),
    ])
    cancelled = CodingAgent(first, InterruptedTools(workspace, HostExecutor(workspace, trusted=True)),
                            session, max_steps=3).run("Fix value; keep the test assertions")
    assert cancelled.status == "cancelled" and not session.verification_passed()
    assert session.environment() == {"version": 1, "sandbox": "host", "image_id": ""}

    resumed_model = Model([
        call("run_tests", {"path": "tests"}, 3),
        call("edit_file", {"path": "app.py", "old_text": "return 0", "new_text": "return 1"}, 4),
        call("run_tests", {"path": "tests"}, 5),
        ModelTurn({"role": "assistant", "content": "Repaired and verified."},
                  "stop", "fixture", "composition", None),
    ])
    restored = SessionStore(tmp_path / "state", session.session_id)
    result = CodingAgent(resumed_model, ToolRegistry(workspace, HostExecutor(workspace, trusted=True)),
                         restored, max_steps=4).run(resume=True)
    assert result.status == "completed" and restored.verification_passed()
    assert test.read_bytes() == original_tests
    first_notice = resumed_model.requests[0][1]["content"]
    assert "Turn 1/4" in first_notice and "missing" in first_notice
    assert "JUnit failures" in resumed_model.requests[1][-1]["content"]
    assert "Final-answer-only" in resumed_model.requests[-1][1]["content"]
    assert resumed_model.schemas[-1] == []
    events = restored.trace_events()
    assert [event["ok"] for event in events if event["event"] == "tool"] == [True, False, True, True]
    assert "assert 0 == 1" not in restored.trace_path.read_text()
    assert not any(str(message.get("content", "")).startswith("Runtime budget (")
                   for message in restored.load()[0])
    verification = [event for event in SessionStore._complete_events(restored.path)
                    if event.get("type") == "verification"]
    assert verification and all(event["evidence"] == "junit-workspace-v2" for event in verification)
    assert all(len(event["workspace_sha256"]) == 64 for event in verification if event["passed"])
