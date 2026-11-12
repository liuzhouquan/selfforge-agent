"""Tests for context token budget awareness and proactive guidance (SF-RUN-06)."""

import json
from pathlib import Path

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.control import budget_notice, evaluate_context_pressure
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry

PREFIX = "Runtime budget (generated for this turn):"


def call(name: str, arguments: dict, number: int) -> ModelTurn:
    return ModelTurn({
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": f"call-{number}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }, "tool_calls", "fixture", "token-budget", None)


def answer(text: str = "Finished.") -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": text},
                     "stop", "fixture", "token-budget", None)


class ScriptedModel:
    def __init__(self, turns: list[ModelTurn]):
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self.schemas: list[list[dict]] = []

    def complete(self, messages, tools, timeout):
        assert timeout > 0
        self.requests.append(list(messages))
        self.schemas.append(tools)
        return next(self.turns)


def notices(request: list[dict]) -> list[str]:
    return [
        message["content"]
        for message in request
        if message.get("role") == "system"
        and str(message.get("content", "")).startswith(PREFIX)
    ]


def test_evaluate_context_pressure_normal_usage() -> None:
    res = evaluate_context_pressure(3000, 8000)
    assert res is None


def test_evaluate_context_pressure_high_usage() -> None:
    res = evaluate_context_pressure(6400, 8000)
    assert res is not None
    assert res["estimated_tokens"] == 6400
    assert res["token_budget"] == 8000
    assert res["ratio"] == 0.8
    assert res["percent"] == 80
    assert res["was_compacted"] is False
    assert res["high_pressure"] is True


def test_evaluate_context_pressure_custom_watermark() -> None:
    # 5000 / 8000 = 62.5%
    below = evaluate_context_pressure(5000, 8000, high_watermark=0.7)
    assert below is None

    above = evaluate_context_pressure(5000, 8000, high_watermark=0.6)
    assert above is not None
    assert above["high_pressure"] is True
    assert above["percent"] == 62


def test_evaluate_context_pressure_was_compacted() -> None:
    res = evaluate_context_pressure(4000, 8000, was_compacted=True)
    assert res is not None
    assert res["estimated_tokens"] == 4000
    assert res["was_compacted"] is True
    assert res["high_pressure"] is False


def test_evaluate_context_pressure_compacted_and_high_usage() -> None:
    res = evaluate_context_pressure(6800, 8000, was_compacted=True)
    assert res is not None
    assert res["was_compacted"] is True
    assert res["high_pressure"] is True
    assert res["percent"] == 85


def test_evaluate_context_pressure_invalid_budget() -> None:
    assert evaluate_context_pressure(100, 0) is None
    assert evaluate_context_pressure(100, -500) is None


def test_budget_notice_renders_high_context_pressure_guidance() -> None:
    info = evaluate_context_pressure(6400, 8000)
    notice = budget_notice(
        step=1,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        context_info=info,
    )
    content = notice["content"]
    assert "High context window usage:" in content
    assert "6400/8000 tokens (80%)" in content
    assert "Avoid dumping full files or large command outputs" in content


def test_budget_notice_renders_compacted_context_guidance() -> None:
    info = evaluate_context_pressure(4000, 8000, was_compacted=True)
    notice = budget_notice(
        step=2,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        context_info=info,
    )
    content = notice["content"]
    assert "Context compaction active:" in content
    assert "earlier conversation history has been summarized" in content
    assert "4000 tokens currently used" in content
    assert "Keep responses focused on required file edits and run_tests verification" in content


def test_budget_notice_renders_compacted_and_high_pressure_guidance() -> None:
    info = evaluate_context_pressure(6800, 8000, was_compacted=True)
    notice = budget_notice(
        step=3,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        context_info=info,
    )
    content = notice["content"]
    assert "Context compaction active:" in content
    assert "Context usage remains high" in content
    assert "6800/8000 tokens, 85%" in content
    assert "prevent loss of recent turn details" in content


def test_budget_notice_omits_context_guidance_when_normal() -> None:
    notice = budget_notice(
        step=1,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=False,
        context_info=None,
    )
    content = notice["content"]
    assert "context window" not in content.lower()
    assert "compaction" not in content.lower()


def test_budget_notice_omits_context_guidance_on_final_only_turn() -> None:
    notice = budget_notice(
        step=5,
        max_steps=5,
        remaining_seconds=100,
        tests_passed=True,
        context_info=None,  # Final only turns suppress context advice
    )
    content = notice["content"]
    assert "Final-answer-only turn" in content
    assert "Context compaction" not in content
    assert "High context window" not in content


def test_coding_agent_traces_context_tokens_and_injects_guidance(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def solve(): return 42\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_mod.py").write_text(
        "from mod import solve\ndef test_solve(): assert solve() == 42\n",
        encoding="utf-8",
    )
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    model = ScriptedModel([
        call("read_file", {"path": "mod.py"}, 1),
        call("run_tests", {"path": "tests"}, 2),
        answer("Verified solve() return 42."),
    ])

    agent = CodingAgent(model, tools, session, max_steps=4)
    # Set a compact token budget to test high watermark triggers
    agent.context.token_budget = 600

    result = agent.run("Verify solve()")
    assert result.status == "completed"

    # Verify trace events recorded context metrics
    turn_budgets = [e for e in session.trace_events() if e.get("event") == "turn_budget"]
    assert len(turn_budgets) == 3
    for event in turn_budgets:
        assert "context_tokens" in event
        assert event["context_tokens"] > 0
        assert event["context_budget"] == 600
        assert "context_compacted" in event

    # Verify that durable messages saved in session store NEVER contain transient notices
    saved_messages, _ = session.load()
    for msg in saved_messages:
        content = str(msg.get("content", ""))
        assert not content.startswith(PREFIX)
        assert "High context window usage" not in content
        assert "Context compaction active" not in content


def test_first_compacted_request_and_resume_report_actual_history(tmp_path):
    (tmp_path / 'large.py').write_text('value = 1\n' * 500)
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    model = ScriptedModel([call('read_file', {'path': 'large.py'}, 1), answer('Inspected')])
    agent = CodingAgent(model, tools, session, max_steps=4)
    agent.context.token_budget = 700
    result = agent.run('Inspect the source')
    assert result.status == 'unverified'
    event = [e for e in session.trace_events() if e['event'] == 'turn_budget'][-1]
    assert event['context_compacted'] is True
    request = model.requests[-1]
    notice = next(m for m in request if m['role'] == 'system' and 'Runtime budget' in m['content'])
    assert 'Context compaction active:' in notice['content']
    history = [m for m in request if m is not notice]
    assert event['context_tokens'] == agent.context.estimate(history)
    assert agent.context.estimate(request) <= agent.context.token_budget
    resumed = ScriptedModel([answer('Still inspected')])
    restored = CodingAgent(resumed, tools, session, max_steps=4)
    restored.context.token_budget = 700
    assert restored.run('', resume=True).status == 'unverified'
    event = [e for e in session.trace_events() if e['event'] == 'turn_budget'][-1]
    assert event['context_compacted'] is True
