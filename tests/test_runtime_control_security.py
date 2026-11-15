"""Security and input defense tests for runtime control flow and guidance (SF-CTRL-01)."""

from __future__ import annotations

import json

from selfforge_agent.runtime.control import (
    _canonical_args,
    budget_notice,
    detect_edit_failure_loop,
    detect_tool_loop,
    evaluate_context_pressure,
)


def test_canonical_args_defense() -> None:
    # 1. Enormous string argument (> 64 KiB)
    huge_str = "a" * (70 * 1024)
    canon, parsed = _canonical_args(huge_str)
    assert len(canon) <= 65_536
    assert parsed == {}

    # 2. Control characters and null bytes in string argument
    bad_str = '{"path": "foo\x00\r\nbar.py"}'
    canon, parsed = _canonical_args(bad_str)
    assert "\x00" not in canon
    assert "\x00" not in str(parsed)

    # 3. Deeply nested structure
    nested: dict = {}
    curr = nested
    for i in range(100):
        curr["k"] = {"lvl": i}
        curr = curr["k"]
    canon, _ = _canonical_args(nested)
    assert isinstance(canon, str)
    assert len(canon) <= 65_536


def test_detect_tool_loop_defense() -> None:
    # 1. Non-list messages or list with non-dict elements
    assert detect_tool_loop(None) is None  # type: ignore[arg-type]
    assert detect_tool_loop("invalid") is None  # type: ignore[arg-type]
    assert detect_tool_loop([None, 123, "text"]) is None  # type: ignore[list-item]

    # 2. Malformed min_repeats
    sample_msgs = [
        {"role": "user", "content": "search"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "data"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c2",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "data"},
    ]
    # min_repeats with boolean or non-positive should fall back safely to 2
    for bad_repeats in (True, False, 0, -1, "2"):
        res = detect_tool_loop(sample_msgs, min_repeats=bad_repeats)  # type: ignore[arg-type]
        assert res is not None
        assert res["tool"] == "read_file"
        assert res["count"] == 2

    # 3. Malformed tool calls without function dict or name
    broken_msgs = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "tool_calls": [{"id": "2", "function": None}]},
        {"role": "assistant", "tool_calls": [{"id": "3", "function": {"name": None}}]},
    ]
    assert detect_tool_loop(broken_msgs) is None


def test_detect_edit_failure_loop_defense() -> None:
    # 1. Non-list messages or list with non-dict elements
    assert detect_edit_failure_loop(None) is None  # type: ignore[arg-type]
    assert detect_edit_failure_loop("invalid") is None  # type: ignore[arg-type]
    assert detect_edit_failure_loop([None, 123]) is None  # type: ignore[list-item]

    # 2. Path traversal in failed edits
    traversal_msgs = [
        {"role": "user", "content": "Fix"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "edit_file", "arguments": json.dumps({"path": "foo/../app.py"})},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "Tool error: conflict"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "c2",
                "function": {"name": "edit_file", "arguments": json.dumps({"path": "./app.py"})},
            }],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "Tool error: conflict"},
    ]
    res = detect_edit_failure_loop(traversal_msgs)
    assert res is not None
    # Path should normalize foo/../app.py and ./app.py both to "app.py"
    assert res["path"] == "app.py"
    assert res["count"] == 2

    # 3. Path with null bytes or control characters
    null_byte_msgs = [
        {"role": "user", "content": "Fix"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "edit_file", "arguments": '{"path": "app\\u0000.py"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "Tool error: conflict"},
    ]
    # Should not crash on null byte path
    assert detect_edit_failure_loop(null_byte_msgs) is None

    # 4. Malformed min_failures
    for bad_mf in (True, False, 0, -1, "2"):
        res = detect_edit_failure_loop(traversal_msgs, min_failures=bad_mf)  # type: ignore[arg-type]
        assert res is not None
        assert res["path"] == "app.py"


def test_evaluate_context_pressure_defense() -> None:
    # 1. Non-finite high_watermark
    for bad_hw in (float("nan"), float("inf"), -0.5, 0.0, 2.0, "0.75"):
        # Should not crash, should fall back to default (0.75)
        res = evaluate_context_pressure(6400, 8000, high_watermark=bad_hw)  # type: ignore[arg-type]
        assert res is not None
        assert res["high_pressure"] is True

    # 2. Boolean token budget / estimated tokens
    assert evaluate_context_pressure(True, 8000) is None  # type: ignore[arg-type]
    assert evaluate_context_pressure(6400, True) is None  # type: ignore[arg-type]
    assert evaluate_context_pressure(-100, 8000) is None


def test_budget_notice_defense() -> None:
    # 1. Non-finite or negative remaining_seconds
    for bad_sec in (float("nan"), float("inf"), -10.0, True, False, "100"):
        notice = budget_notice(
            step=1,
            max_steps=5,
            remaining_seconds=bad_sec,  # type: ignore[arg-type]
            tests_passed=False,
        )
        assert "0 seconds left" in notice["content"]

    # 2. Oversized path in edit_failure
    huge_path = "a" * 10_000 + ".py"
    notice = budget_notice(
        step=2,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        edit_failure={"path": huge_path, "count": 2},
    )
    # The path in notice content should be truncated to prevent prompt blowup
    assert len(notice["content"]) < 2000
    assert "Consecutive edit failures on" in notice["content"]

    # 3. Oversized tool name in tool_loop
    huge_tool = "t" * 10_000
    notice = budget_notice(
        step=2,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        tool_loop={"tool": huge_tool, "count": 2},
    )
    assert len(notice["content"]) < 2000
    assert "Repeated read-only tool call detected" in notice["content"]
