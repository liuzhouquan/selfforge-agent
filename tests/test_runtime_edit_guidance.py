"""Tests for consecutive edit failure loop detection and transient guidance (SF-RUN-05)."""

import json
from pathlib import Path

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.control import budget_notice, detect_edit_failure_loop
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


def test_detect_edit_failure_loop_empty_or_no_failures() -> None:
    assert detect_edit_failure_loop([]) is None
    assert detect_edit_failure_loop([{"role": "user", "content": "Fix bug"}]) is None

    # Successful edit does not trigger
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "a", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Edited app.py"),
    ]
    assert detect_edit_failure_loop(msgs) is None


def test_detect_edit_failure_loop_single_failure_below_threshold() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
    ]
    # Default min_failures=2, so 1 failure should return None
    assert detect_edit_failure_loop(msgs) is None


def test_detect_edit_failure_loop_triggers_after_consecutive_failures() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
    ]
    detected = detect_edit_failure_loop(msgs)
    assert detected is not None
    assert detected["path"] == "app.py"
    assert detected["count"] == 2


def test_detect_edit_failure_loop_normalizes_relative_paths() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "./app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
    ]
    detected = detect_edit_failure_loop(msgs)
    assert detected is not None
    assert detected["path"] == "app.py"
    assert detected["count"] == 2


def test_detect_edit_failure_loop_resets_on_read_file() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
        # Model performs inspection on app.py
        _tool_turn("read_file", {"path": "app.py"}, "c3"),
        _tool_resp("c3", "def run():\n    return 0\n"),
    ]
    # Reading app.py resets the consecutive failure count for app.py
    assert detect_edit_failure_loop(msgs) is None


def test_detect_edit_failure_loop_read_other_file_does_not_reset_target() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        # Reading another file does not inspect app.py
        _tool_turn("read_file", {"path": "other.py"}, "c2"),
        _tool_resp("c2", "other content"),
        # Another failure on app.py
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c3"),
        _tool_resp("c3", "Tool error: old_text must occur exactly once"),
    ]
    detected = detect_edit_failure_loop(msgs)
    assert detected is not None
    assert detected["path"] == "app.py"
    assert detected["count"] == 2


def test_detect_edit_failure_loop_resets_on_successful_edit() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
        # Successful edit on app.py
        _tool_turn("edit_file", {"path": "app.py", "old_text": "actual", "new_text": "b"}, "c3"),
        _tool_resp("c3", "Edited app.py"),
    ]
    assert detect_edit_failure_loop(msgs) is None


def test_detect_edit_failure_loop_resets_on_new_user_message() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad1", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "app.py", "old_text": "bad2", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
        {"role": "user", "content": "Try a different approach"},
    ]
    assert detect_edit_failure_loop(msgs) is None


def test_detect_edit_failure_loop_tracks_files_independently() -> None:
    msgs = [
        {"role": "user", "content": "Fix bug"},
        _tool_turn("edit_file", {"path": "a.py", "old_text": "bad", "new_text": "b"}, "c1"),
        _tool_resp("c1", "Tool error: old_text must occur exactly once"),
        _tool_turn("edit_file", {"path": "b.py", "old_text": "bad", "new_text": "b"}, "c2"),
        _tool_resp("c2", "Tool error: old_text must occur exactly once"),
    ]
    # 1 failure on a.py, 1 failure on b.py -> neither has reached 2
    assert detect_edit_failure_loop(msgs) is None


def test_budget_notice_renders_edit_failure_guidance() -> None:
    notice_no_fail = budget_notice(step=2, max_steps=5, remaining_seconds=120, tests_passed=False)
    assert "Consecutive edit failures on" not in notice_no_fail["content"]

    notice_with_fail = budget_notice(
        step=3,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        edit_failure={"path": "app.py", "count": 2},
    )
    assert "Consecutive edit failures on 'app.py' (2 times)." in notice_with_fail["content"]
    assert "Use read_file to inspect the exact lines and indentation of 'app.py' and review the tool error before further edits; respect permission and approval restrictions." in notice_with_fail["content"]


def test_budget_notice_suppressed_on_final_only() -> None:
    # When final_only is active (remaining_turns=1 and tests_passed=True), edit_failure guidance must be suppressed
    notice = budget_notice(
        step=5,
        max_steps=5,
        remaining_seconds=60,
        tests_passed=True,
        edit_failure={"path": "app.py", "count": 2},
    )
    assert "Consecutive edit failures on" not in notice["content"]
    assert "Final-answer-only turn" in notice["content"]


class MockModel:
    def __init__(self, turns: list[ModelTurn]):
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []

    def complete(self, messages, tools, timeout):
        self.requests.append(list(messages))
        return next(self.turns)


def test_coding_agent_warns_on_consecutive_edit_failures_and_traces(tmp_path: Path) -> None:
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

    # Step 1: edit_file app.py with wrong old_text -> fails
    # Step 2: edit_file app.py with wrong old_text -> fails (consecutive = 2)
    # Step 3: read_file app.py -> inspects and resets failure count
    # Step 4: edit_file app.py with correct old_text -> succeeds
    # Step 5: run_tests -> passes
    # Step 6: final answer
    model = MockModel([
        make_turn("edit_file", {"path": "app.py", "old_text": "non_existent_1", "new_text": "x"}, 1),
        make_turn("edit_file", {"path": "app.py", "old_text": "non_existent_2", "new_text": "y"}, 2),
        make_turn("read_file", {"path": "app.py"}, 3),
        make_turn("edit_file", {"path": "app.py", "old_text": "return 0", "new_text": "return 1"}, 4),
        make_turn("run_tests", {"path": "tests"}, 5),
        answer_turn(),
    ])

    agent = CodingAgent(model, tools, session, max_steps=6)
    result = agent.run("Fix run()")
    assert result.status == "completed"

    # Step 1 request: no failure guidance yet
    step1_system = [m["content"] for m in model.requests[0] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step1_system) == 1
    assert "Consecutive edit failures on" not in step1_system[0]

    # Step 2 request: only 1 failure so far, no failure guidance yet
    step2_system = [m["content"] for m in model.requests[1] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step2_system) == 1
    assert "Consecutive edit failures on" not in step2_system[0]

    # Step 3 request: 2 consecutive edit failures on app.py! Guidance MUST be present!
    step3_system = [m["content"] for m in model.requests[2] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step3_system) == 1
    assert "Consecutive edit failures on 'app.py' (2 times)" in step3_system[0]
    assert "Use read_file to inspect the exact lines and indentation of 'app.py'" in step3_system[0]

    # Step 4 request: read_file was called on Step 3, failure count MUST be reset!
    step4_system = [m["content"] for m in model.requests[3] if m.get("role") == "system" and "Runtime budget" in m.get("content", "")]
    assert len(step4_system) == 1
    assert "Consecutive edit failures on" not in step4_system[0]

    # Invariant: transient guidance must NOT be saved in durable session messages
    saved_session_messages, _ = session.load()
    for msg in saved_session_messages:
        content = str(msg.get("content") or "")
        assert "Consecutive edit failures on" not in content

    # Verify trace events recorded edit_failure correctly
    turn_budgets = [e for e in session.trace_events() if e.get("event") == "turn_budget"]
    assert len(turn_budgets) == 6
    assert "app.py" not in json.dumps(turn_budgets)
    assert turn_budgets[0]["edit_failure_count"] == 0
    assert turn_budgets[1]["edit_failure_count"] == 0
    assert turn_budgets[2]["edit_failure_count"] == 2
    assert turn_budgets[3]["edit_failure_count"] == 0
    assert turn_budgets[4]["edit_failure_count"] == 0
    assert turn_budgets[5]["edit_failure_count"] == 0
