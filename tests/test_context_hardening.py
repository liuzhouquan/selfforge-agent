"""Deep security hardening, schema formalization and defensive reproduction tests for ContextManager (SF-CTX-02)."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.runtime.context import (
    MAX_CONTEXT_BUDGET,
    MAX_ESTIMATE_CHARS,
    CompactionReport,
    ContextManager,
    ContextMetrics,
    _recent_interactions,
    estimate_message_tokens,
    estimate_tokens,
)


class TestTokenEstimationHardening:
    """SF-CTX-02-A1: Token estimation boundary defenses, null byte filtering and recursion safety."""

    def test_estimate_tokens_rejects_null_byte(self):
        with pytest.raises(ValueError, match="null byte"):
            estimate_tokens("hello\0world")

    def test_estimate_tokens_rejects_oversized_text(self):
        huge_text = "a" * (MAX_ESTIMATE_CHARS + 1)
        with pytest.raises(ValueError, match="maximum token estimation limit"):
            estimate_tokens(huge_text)

    def test_estimate_message_tokens_rejects_invalid_role_or_null_bytes(self):
        for bad_role in ["", "\0", "user\0", "   "]:
            with pytest.raises(ValueError, match="Invalid role in message"):
                estimate_message_tokens({"role": bad_role, "content": "hello"})

        with pytest.raises(TypeError, match="role must be a string"):
            estimate_message_tokens({"role": 123, "content": "hello"})

    def test_estimate_message_tokens_handles_deep_nesting_without_overflow(self):
        # Deeply nested content list
        deep: list = ["nested text"]
        for _ in range(50):
            deep = [deep]
        # Should evaluate safely without RecursionError or crash
        tokens = estimate_message_tokens({"role": "user", "content": deep})
        assert tokens > 0


class TestRecentInteractionsHardening:
    """SF-CTX-02-A2: Recent interactions pairing enforcement, duplicate ID defense and orphan tool response rejection."""

    def test_recent_interactions_rejects_duplicate_tool_call_ids_in_assistant(self):
        # Assistant declaring two tool calls with the same id is malformed
        calls = [
            {"id": "duplicate_id", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "duplicate_id", "type": "function", "function": {"name": "edit_file", "arguments": "{}"}},
        ]
        messages = [
            {"role": "user", "content": "Start task"},
            {"role": "assistant", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "duplicate_id", "content": "result 1"},
        ]
        assert _recent_interactions(messages, 1) == []

    def test_recent_interactions_rejects_duplicate_tool_responses(self):
        # Two tool messages answering the same call_id
        calls = [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]
        messages = [
            {"role": "user", "content": "Start task"},
            {"role": "assistant", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "call_1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "call_1", "content": "result 2 (duplicate)"},
        ]
        assert _recent_interactions(messages, 1) == []

    def test_recent_interactions_rejects_orphan_tool_response(self):
        # Tool response for an undeclared tool call id
        calls = [
            {"id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]
        messages = [
            {"role": "user", "content": "Start task"},
            {"role": "assistant", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "call_rogue", "content": "result rogue"},
        ]
        assert _recent_interactions(messages, 1) == []


class TestCompactionOrderingAndToolSanitization:
    """SF-CTX-02-A3: Summary positioning between system instructions and user task, tool summarization & ANSI stripping."""

    def test_compaction_places_summary_between_system_and_user_prompts(self):
        manager = ContextManager(token_budget=600)
        messages = [
            {"role": "system", "content": "System instruction 1: You are a coding agent."},
            {"role": "system", "content": "System instruction 2: Never modify protected files."},
            {"role": "user", "content": "User task 1: Implement feature X."},
            {"role": "assistant", "content": "Detailed thinking and investigation " * 500},
            {"role": "user", "content": "User task 2: Ensure test passes."},
        ]

        compacted, changed = manager.compact(messages)
        assert changed is True
        assert manager.estimate(compacted) <= manager.token_budget

        # Verify order: base system messages -> summary system message -> user messages
        roles = [m["role"] for m in compacted]
        assert roles[0] == "system"
        assert roles[1] == "system"
        assert roles[2] == "system"  # Compressed history summary
        assert "Earlier steps (compressed;" in compacted[2]["content"]
        assert roles[3] == "user"
        assert compacted[3]["content"] == "User task 1: Implement feature X."
        assert roles[4] == "user"
        assert compacted[4]["content"] == "User task 2: Ensure test passes."

    def test_compaction_sanitizes_ansi_escape_codes_in_tool_output(self):
        manager = ContextManager(token_budget=600)
        messages = [
            {"role": "system", "content": "You are a coding agent."},
            {"role": "user", "content": "Run tests and fix errors."},
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_tests", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "\x1b[31;1mFAILED tests/test_foo.py\x1b[0m\nDetails: error line"},
            {"role": "assistant", "content": "Investigating the failure " * 300},
        ]
        compacted, changed = manager.compact(messages)
        assert changed is True
        summary_msg = next(m for m in compacted if "Earlier steps" in str(m.get("content", "")))
        assert "\x1b[" not in summary_msg["content"]
        assert "FAILED tests/test_foo.py" in summary_msg["content"]

    def test_compaction_summarizes_write_file_and_run_command(self):
        manager = ContextManager(token_budget=600)
        messages = [
            {"role": "system", "content": "System instructions."},
            {"role": "user", "content": "Create a file and run a command."},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "foo.txt", "content": "abc"}'}},
                {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": '{"command": "pytest"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "File foo.txt created."},
            {"role": "tool", "tool_call_id": "c2", "content": "1 passed in 0.01s"},
            {"role": "assistant", "content": "Completed operations " * 300},
        ]
        compacted, changed = manager.compact(messages)
        assert changed is True
        summary_msg = next(m for m in compacted if "Earlier steps" in str(m.get("content", "")))
        assert "Wrote file: foo.txt" in summary_msg["content"]
        assert "Ran command: pytest" in summary_msg["content"]


class TestCompactionReportAndContextMetrics:
    """SF-CTX-02-A4: Formal Pydantic models for ContextMetrics and CompactionReport, manager extension & budget ceiling."""

    def test_context_manager_rejects_excessive_token_budget(self):
        with pytest.raises(ValueError, match="between 500 and"):
            ContextManager(MAX_CONTEXT_BUDGET + 1)

    def test_context_metrics_model_validation(self):
        valid = {
            "total_messages": 5,
            "estimated_tokens": 400,
            "token_budget": 1000,
            "reserve_tokens": 100,
            "utilization": 0.4,
            "is_under_pressure": False,
            "was_compacted": False,
        }
        metrics = ContextMetrics.model_validate(valid)
        assert metrics.total_messages == 5
        assert metrics.utilization == 0.4

        with pytest.raises(ValidationError):
            ContextMetrics.model_validate({**valid, "utilization": -0.1})

    def test_compaction_report_model_validation(self):
        valid = {
            "was_compacted": True,
            "original_messages_count": 10,
            "compacted_messages_count": 4,
            "original_tokens": 1500,
            "compacted_tokens": 550,
            "tokens_saved": 950,
            "preserved_turns": 1,
            "summary_tokens": 60,
        }
        report = CompactionReport.model_validate(valid)
        assert report.was_compacted is True
        assert report.tokens_saved == 950

        with pytest.raises(ValidationError):
            CompactionReport.model_validate({**valid, "tokens_saved": -10})

    def test_get_metrics_and_compact_with_report(self):
        manager = ContextManager(token_budget=1000)
        messages = [
            {"role": "system", "content": "System constraints."},
            {"role": "user", "content": "Task description."},
            {"role": "assistant", "content": "Long interaction step " * 400},
        ]
        metrics = manager.get_metrics(messages, reserve_tokens=100)
        assert isinstance(metrics, ContextMetrics)
        assert metrics.total_messages == 3
        assert metrics.estimated_tokens > 500
        assert metrics.token_budget == 1000
        assert metrics.reserve_tokens == 100

        compacted, report = manager.compact_with_report(messages, reserve_tokens=100)
        assert isinstance(report, CompactionReport)
        assert report.was_compacted is True
        assert report.original_tokens > report.compacted_tokens
        assert report.tokens_saved == report.original_tokens - report.compacted_tokens
        assert manager.estimate(compacted) <= 900
