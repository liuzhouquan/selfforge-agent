"""Security, input validation, and defensive boundary tests for ContextManager."""

import pytest

from selfforge_agent.runtime.context import (
    ContextManager,
    _recent_interactions,
    estimate_message_tokens,
    estimate_tokens,
)


def test_recent_interactions_without_user_message_returns_empty() -> None:
    # Baseline crashes with ValueError: max() arg is an empty sequence
    messages = [
        {"role": "system", "content": "You are an agent"},
        {"role": "assistant", "content": "Hello without user prompt"},
    ]
    assert _recent_interactions(messages, 1) == []


def test_recent_interactions_with_malformed_messages_returns_empty() -> None:
    # Baseline crashes with AttributeError on non-dict items or bad tool calls
    messages = [
        {"role": "user", "content": "Start"},
        None,
        "not a dict",
        {"role": "assistant", "tool_calls": ["invalid_call_item"]},
        {"role": "tool", "tool_call_id": 123, "content": "result"},
    ]
    assert _recent_interactions(messages, 1) == []


def test_recent_interactions_invalid_limits() -> None:
    messages = [
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": "Done"},
    ]
    assert _recent_interactions(messages, 0) == []
    assert _recent_interactions(messages, -5) == []
    assert _recent_interactions(messages, True) == []  # bool must not be accepted as positive int
    assert _recent_interactions(messages, "1") == []  # type: ignore[arg-type]


def test_estimate_tokens_input_validation() -> None:
    assert estimate_tokens(None) == 0  # type: ignore[arg-type]
    for invalid in [123, ["text"], {"key": "val"}, object()]:
        with pytest.raises(TypeError, match="text must be a str"):
            estimate_tokens(invalid)  # type: ignore[arg-type]


def test_estimate_message_tokens_fail_closed() -> None:
    for invalid in [None, "string", 123, [1, 2]]:
        with pytest.raises(TypeError, match="message must be a dict"):
            estimate_message_tokens(invalid)  # type: ignore[arg-type]

    # Malformed tool_calls and functions should not crash
    malformed_msg = {
        "role": "assistant",
        "tool_calls": [
            None,
            "not_a_dict",
            {"function": None},
            {"function": "string_not_dict"},
            {"function": {"name": 123, "arguments": 456}},
        ],
    }
    assert estimate_message_tokens(malformed_msg) > 0


def test_context_manager_init_type_defense() -> None:
    for invalid in [True, False, float("nan"), float("inf"), 1000.5, "8000", -1, 0, 499]:
        with pytest.raises(ValueError, match="Context budget must be an integer of at least 500"):
            ContextManager(invalid)  # type: ignore[arg-type]


def test_context_manager_estimate_input_defense() -> None:
    manager = ContextManager(1000)
    for invalid in [None, "not_a_list", 123, {"role": "user"}]:
        with pytest.raises(TypeError, match="messages must be a list"):
            manager.estimate(invalid)  # type: ignore[arg-type]


def test_context_manager_compact_input_defense() -> None:
    manager = ContextManager(1000)
    valid_msgs = [
        {"role": "system", "content": "Instruction"},
        {"role": "user", "content": "Task"},
    ]

    # Non-list messages
    with pytest.raises(TypeError, match="messages must be a list"):
        manager.compact(None)  # type: ignore[arg-type]

    # Messages containing non-dict items
    with pytest.raises(TypeError, match="must be a dict"):
        manager.compact([valid_msgs[0], "not_a_dict", valid_msgs[1]])

    # Invalid reserve_tokens
    for bad_reserve in [True, False, 10.5, float("nan"), -1, 1000, 1500, "100"]:
        with pytest.raises(ValueError, match="Reserved tokens must leave a positive context budget"):
            manager.compact(valid_msgs, reserve_tokens=bad_reserve)  # type: ignore[arg-type]

    # Invalid preserve_recent_turns
    for bad_turns in [True, -1, 1.5, "1", None]:
        with pytest.raises(ValueError, match="preserve_recent_turns must be a non-negative integer"):
            manager.compact(valid_msgs, preserve_recent_turns=bad_turns)  # type: ignore[arg-type]


def test_compact_iterative_halving_under_tight_budget() -> None:
    # Under tight budget where anchors occupy most of budget,
    # iterative halving should reduce allowed summary down below 50 to fit,
    # rather than failing prematurely at allowed <= 50.
    manager = ContextManager(550)
    messages = [
        {"role": "system", "content": "You are a coding agent with precise constraints."},
        {"role": "user", "content": "Task: " + "a" * 800},  # ~200 tokens
        {"role": "assistant", "content": "Action 1: " + "b" * 400},
        {"role": "tool", "content": "Output 1: " + "c" * 400},
        {"role": "assistant", "content": "Action 2: " + "d" * 400},
    ]
    # Anchors take ~220 tokens. Budget is 550.
    # Older intermediates take ~300 tokens. Total is ~520 > budget (when reserves added).
    compacted, changed = manager.compact(messages, reserve_tokens=150)
    assert changed is True
    assert manager.estimate(compacted) <= 400
    assert compacted[0] == messages[0]
    assert [m for m in compacted if m["role"] == "user"] == [messages[1]]


def test_compact_handles_malformed_intermediate_messages() -> None:
    manager = ContextManager(1000)
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Task description."},
        # Intermediates with non-dict tool calls, weird arguments, etc.
        {"role": "assistant", "content": "x" * 2000, "tool_calls": [None, "invalid_call", {"function": "not_a_dict"}]},
        {"role": "assistant", "tool_calls": [{"function": {"name": "edit_file", "arguments": "invalid json{"}}]},
        {"role": "tool", "content": "Some test output\nLine 2"},
        {"role": "assistant", "content": "Done."},
    ]
    compacted, changed = manager.compact(messages, reserve_tokens=600)
    assert changed is True
    assert manager.estimate(compacted) <= 400
    assert compacted[0] == messages[0]
    assert [m for m in compacted if m["role"] == "user"] == [messages[1]]


def test_compaction_preserves_every_system_constraint_and_user_task():
    manager = ContextManager(500)
    anchors = [
        {"role": "system", "content": "Main instruction"},
        {"role": "system", "content": "Never edit protected files"},
        {"role": "user", "content": "First task"},
        {"role": "user", "content": "Also preserve configuration"},
    ]
    compacted, changed = manager.compact(anchors + [{"role": "assistant", "content": "x " * 2000}])
    assert changed
    assert all(anchor in compacted for anchor in anchors)
    assert manager.estimate(compacted) <= 500


@pytest.mark.parametrize("function", [["bad"], {"name": 7, "arguments": "{}"}, {"name": "read_file", "arguments": []}])
def test_recent_native_call_rejects_invalid_function_shape(function):
    messages = [{"role": "user", "content": "task"},
                {"role": "assistant", "tool_calls": [{"id": "a", "type": "function", "function": function}]},
                {"role": "tool", "tool_call_id": "a", "content": "result"}]
    assert _recent_interactions(messages, 1) == []


def test_compaction_preserves_complete_native_multicall_with_reversed_replies():
    calls = [{"id": key, "type": "function", "function": {"name": "read_file", "arguments": "{}"}} for key in ("a", "b")]
    recent = [{"role": "assistant", "tool_calls": calls},
              {"role": "tool", "tool_call_id": "b", "content": "second"},
              {"role": "tool", "tool_call_id": "a", "content": "first"}]
    messages = [{"role": "system", "content": "constraints"},
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "old " * 3000}, *recent]
    compacted, changed = ContextManager(500).compact(messages, preserve_recent_turns=1)
    assert changed
    assert compacted[-3:] == recent
