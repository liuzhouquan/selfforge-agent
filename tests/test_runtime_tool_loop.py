"""Tests for read-only tool loop detection and transient guidance (SF-RUN-04)."""

import json
from pathlib import Path

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.control import budget_notice, detect_tool_loop
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


def _tool_turn(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }


def _tool_resp(call_id: str = "call-1", content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_detect_tool_loop_empty_or_no_tools() -> None:
    assert detect_tool_loop([]) is None
    assert detect_tool_loop([{"role": "user", "content": "Fix bug"}]) is None


def test_detect_tool_loop_single_or_different_calls() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("read_file", {"path": "a.py"}, "c1"),
        _tool_resp("c1", "content of a"),
    ]
    assert detect_tool_loop(msgs) is None

    msgs.extend([
        _tool_turn("read_file", {"path": "b.py"}, "c2"),
        _tool_resp("c2", "content of b"),
    ])
    assert detect_tool_loop(msgs) is None


def test_detect_tool_loop_identical_consecutive_calls() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("read_file", {"path": "a.py"}, "c1"),
        _tool_resp("c1", "content of a"),
        _tool_turn("read_file", {"path": "a.py"}, "c2"),
        _tool_resp("c2", "content of a"),
    ]
    loop = detect_tool_loop(msgs)
    assert loop is not None
    assert loop["tool"] == "read_file"
    assert loop["count"] == 2
    assert loop["arguments"] == {"path": "a.py"}


def test_detect_tool_loop_normalizes_argument_ordering() -> None:
    msgs = [
        {"role": "user", "content": "Search for class"},
        _tool_turn("search_code", {"query": "class Foo", "path": "src"}, "c1"),
        _tool_resp("c1", "matches"),
        _tool_turn("search_code", {"path": "src", "query": "class Foo"}, "c2"),
        _tool_resp("c2", "matches"),
    ]
    loop = detect_tool_loop(msgs)
    assert loop is not None
    assert loop["tool"] == "search_code"
    assert loop["count"] == 2


def test_detect_tool_loop_reset_after_edit() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("read_file", {"path": "a.py"}, "c1"),
        _tool_resp("c1", "old content"),
        _tool_turn("edit_file", {"path": "a.py", "old_text": "old", "new_text": "new"}, "c2"),
        _tool_resp("c2", "edited"),
        # Reading a.py after an edit is valid inspection, not a loop
        _tool_turn("read_file", {"path": "a.py"}, "c3"),
        _tool_resp("c3", "new content"),
    ]
    assert detect_tool_loop(msgs) is None


def test_budget_notice_renders_tool_loop_guidance() -> None:
    notice_no_loop = budget_notice(step=2, max_steps=5, remaining_seconds=120, tests_passed=False)
    assert "Repeated read-only tool call detected" not in notice_no_loop["content"]

    notice_with_loop = budget_notice(
        step=3, max_steps=5, remaining_seconds=100, tests_passed=False,
        tool_loop={"tool": "read_file", "count": 2},
    )
    assert "Repeated read-only tool call detected" in notice_with_loop["content"]
    assert "you have called 'read_file' with identical arguments 2 times without intervening edits" in notice_with_loop["content"]
    assert "Avoid duplicate lookups and proceed with required edits (edit_file) or verification (run_tests)" in notice_with_loop["content"]


class MockModel:
    def __init__(self, turns: list[ModelTurn]):
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []

    def complete(self, messages, tools, timeout):
        self.requests.append(list(messages))
        return next(self.turns)


def test_coding_agent_warns_on_repeated_tool_loop_and_traces(tmp_path: Path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("def run():\n    return 0\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("from app import run\n\ndef test_run():\n    assert run() == 1\n")

    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    def make_turn(name: str, args: dict, num: int) -> ModelTurn:
        return ModelTurn({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call-{num}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }],
        }, "tool_calls", "fixture", "test", None)

    def answer_turn() -> ModelTurn:
        return ModelTurn({"role": "assistant", "content": "All done."}, "stop", "fixture", "test", None)

    # Step 1: read_file app.py
    # Step 2: read_file app.py (repeated!)
    # Step 3: edit_file app.py
    # Step 4: run_tests
    # Step 5: answer
    model = MockModel([
        make_turn("read_file", {"path": "app.py"}, 1),
        make_turn("read_file", {"path": "app.py"}, 2),
        make_turn("edit_file", {"path": "app.py", "old_text": "return 0", "new_text": "return 1"}, 3),
        make_turn("run_tests", {"path": "tests"}, 4),
        answer_turn(),
    ])

    agent = CodingAgent(model, tools, session, max_steps=5)
    result = agent.run("Fix run()")
    assert result.status == "completed"

    # Step 1 request (turn 1): no loop guidance
    step1_system = [m["content"] for m in model.requests[0] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step1_system) == 1
    assert "Repeated read-only tool call detected" not in step1_system[0]

    # Step 2 request (turn 2): read_file was only called once so far, no loop guidance yet
    step2_system = [m["content"] for m in model.requests[1] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step2_system) == 1
    assert "Repeated read-only tool call detected" not in step2_system[0]

    # Step 3 request (turn 3): read_file was called on Step 1 and Step 2! Loop guidance MUST be present!
    step3_system = [m["content"] for m in model.requests[2] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step3_system) == 1
    assert "Repeated read-only tool call detected" in step3_system[0]
    assert "'read_file' with identical arguments 2 times" in step3_system[0]

    # Step 4 request (turn 4): edit_file was executed on Step 3! Loop guidance MUST be reset!
    step4_system = [m["content"] for m in model.requests[3] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step4_system) == 1
    assert "Repeated read-only tool call detected" not in step4_system[0]

    # Verify trace event recorded tool_loop
    turn_budgets = [e for e in session.trace_events() if e.get("event") == "turn_budget"]
    assert len(turn_budgets) == 5
    assert turn_budgets[0]["tool_loop"] is None
    assert turn_budgets[1]["tool_loop"] is None
    assert turn_budgets[2]["tool_loop"] == "read_file"
    assert turn_budgets[3]["tool_loop"] is None


def test_old_loop_is_not_reported_after_verification_or_new_task():
    repeated = [_tool_turn("read_file", {"path": "a.py"}, "a"), _tool_resp("a"),
                _tool_turn("read_file", {"path": "a.py"}, "b"), _tool_resp("b")]
    assert detect_tool_loop(repeated)
    assert detect_tool_loop(repeated + [_tool_turn("run_tests", {"path": "tests"}, "c"), _tool_resp("c")]) is None
    assert detect_tool_loop(repeated + [{"role": "user", "content": "New task"}]) is None
