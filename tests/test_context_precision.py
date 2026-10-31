"""Tests for precise tokenization and structured compaction in ContextManager."""

from selfforge_agent.runtime.context import ContextManager, estimate_message_tokens, estimate_tokens


def test_estimate_tokens_handles_code_and_text() -> None:
    # Empty string has 0 tokens
    assert estimate_tokens("") == 0

    # Typical English words (~4 chars/token)
    assert estimate_tokens("hello world") == 2

    # Code indentation: 8 spaces = 2 tokens (4 spaces per indent token)
    assert estimate_tokens("        def foo():") >= 4

    # Punctuation and operators in code
    code = "if a == b and c != d: return [1, 2, 3]"
    tokens = estimate_tokens(code)
    assert 10 <= tokens <= 20

    # CJK characters: 3 Chinese characters typically equal ~3-5 tokens in BPE
    cjk = "修复测试错误"
    cjk_tokens = estimate_tokens(cjk)
    assert 3 <= cjk_tokens <= 8


def test_estimate_message_tokens_includes_format_and_tool_calls() -> None:
    msg_user = {"role": "user", "content": "Fix the bug"}
    assert estimate_message_tokens(msg_user) >= 6  # envelope + content

    msg_tool = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_123",
            "type": "function",
            "function": {"name": "edit_file", "arguments": '{"path": "foo.py"}'},
        }],
    }
    tokens = estimate_message_tokens(msg_tool)
    assert tokens >= 10


def test_richer_compaction_retains_recent_tool_interaction() -> None:
    manager = ContextManager(token_budget=600)
    messages = [
        {"role": "system", "content": "You are a coding agent"},
        {"role": "user", "content": "Fix the add function in calculator.py"},
        # Earlier historical interactions
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "calculator.py"}'},
        }]},
        {"role": "tool", "tool_call_id": "call_1", "content": "def add(a, b):\n    return a - b\n" * 80},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_2", "type": "function",
            "function": {"name": "edit_file", "arguments": '{"path": "calculator.py", "old_text": "-", "new_text": "+"}'},
        }]},
        {"role": "tool", "tool_call_id": "call_2", "content": "File edited successfully"},
        # Most recent interaction that should be preserved for immediate continuity
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_3", "type": "function",
            "function": {"name": "run_tests", "arguments": '{"path": "tests"}'},
        }]},
        {"role": "tool", "tool_call_id": "call_3", "content": "pytest passed 1 test"},
    ]

    compacted, changed = manager.compact(messages, preserve_recent_turns=1)
    assert changed is True
    assert manager.estimate(compacted) <= manager.token_budget

    # Must retain user prompt
    user_msgs = [m for m in compacted if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert "Fix the add function" in user_msgs[0]["content"]

    # Must preserve the most recent assistant tool call and tool response
    assert compacted[-2]["role"] == "assistant"
    assert compacted[-2]["tool_calls"][0]["function"]["name"] == "run_tests"
    assert compacted[-1]["role"] == "tool"
    assert "pytest passed" in compacted[-1]["content"]

    # The compacted summary should capture structured historical actions
    summary_msg = compacted[1]
    assert summary_msg["role"] == "system"
    content = summary_msg["content"]
    assert "calculator.py" in content or "edit_file" in content or "read_file" in content
