"""The model fallback chain shares one call budget and honors cancellation."""

import json
from pathlib import Path
from threading import Event

import pytest

from selfforge_agent.integrations.model_http import ProviderHTTPError
from selfforge_agent.providers import ProviderConfig
from selfforge_agent.runtime import model as model_module
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn, OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


def _providers() -> list[ProviderConfig]:
    return [
        ProviderConfig(name="deepseek", label="DeepSeek", key="test-a", model="a",
                       base_url="https://example.com/a", status_url=""),
        ProviderConfig(name="openrouter", label="OpenRouter", key="test-b", model="b",
                       base_url="https://example.com/b", status_url=""),
    ]


def test_fallback_passes_only_remaining_time_to_next_provider(monkeypatch) -> None:
    clock = [100.0]
    seen: list[float] = []
    monkeypatch.setattr(model_module, "monotonic", lambda: clock[0], raising=False)

    def fake_request(_url, key, **kwargs):
        seen.append(kwargs["timeout"])
        if key == "test-a":
            clock[0] += 0.8
            raise ProviderHTTPError("unavailable", status_code=503)
        clock[0] += 0.1
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Done"}}]}

    monkeypatch.setattr(model_module, "request_json", fake_request)
    turn = OpenAIChatAdapter(_providers()).complete([{"role": "user", "content": "task"}], [], 1)
    assert turn.provider == "openrouter"
    assert seen == pytest.approx([1.0, 0.2])


def test_agent_records_timeout_when_fallback_budget_expires(tmp_path: Path, monkeypatch) -> None:
    clock = [100.0]
    seen: list[str] = []
    monkeypatch.setattr(model_module, "monotonic", lambda: clock[0], raising=False)

    def fake_request(_url, key, **_kwargs):
        seen.append(key)
        clock[0] += 6
        raise ProviderHTTPError("unavailable", status_code=503)

    monkeypatch.setattr(model_module, "request_json", fake_request)
    session = SessionStore(tmp_path)
    agent = CodingAgent(OpenAIChatAdapter(_providers()),
                        ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                        session, time_budget=5)
    result = agent.run("Do a task")
    assert seen == ["test-a"]
    assert result.status == "timed_out"
    assert session.load()[1] == "timed_out"


def test_late_provider_success_cannot_exceed_call_budget(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(model_module, "monotonic", lambda: clock[0], raising=False)

    def fake_request(_url, _key, **_kwargs):
        clock[0] += 1.1
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Late"}}]}

    monkeypatch.setattr(model_module, "request_json", fake_request)
    with pytest.raises(model_module.ModelCallTimedOut):
        OpenAIChatAdapter(_providers()).complete([{"role": "user", "content": "task"}], [], 1)


def test_agent_cancellation_stops_provider_fallback(tmp_path: Path, monkeypatch) -> None:
    cancelled = Event()
    seen: list[str] = []

    def fake_request(_url, key, **_kwargs):
        seen.append(key)
        cancelled.set()
        raise ProviderHTTPError("unavailable", status_code=503)

    monkeypatch.setattr(model_module, "request_json", fake_request)
    session = SessionStore(tmp_path)
    agent = CodingAgent(OpenAIChatAdapter(_providers()),
                        ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                        session)
    # Web jobs replace the Event with a durable cancellation marker after construction.
    agent.cancel = cancelled
    result = agent.run("Do a task")
    assert seen == ["test-a"]
    assert result.status == "cancelled"
    assert session.load()[1] == "cancelled"


def test_agent_timeout_after_prior_turns_records_completed_steps(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world", encoding="utf-8")
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    call_count = 0

    class TimingOutModel:
        def complete(self, messages, schemas, timeout):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return ModelTurn({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{call_count}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "hello.txt"}),
                        },
                    }],
                }, "tool_calls", "fixture", "mock", None)
            raise model_module.ModelCallTimedOut("Turn 3 model call timed out")

    agent = CodingAgent(TimingOutModel(), tools, session, max_steps=5)
    result = agent.run("Read file repeatedly")
    assert result.status == "timed_out"
    assert result.tools_used == 2
    assert result.steps == 2
    assert session.load()[1] == "timed_out"


def test_agent_cancellation_after_prior_turns_records_completed_steps(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world", encoding="utf-8")
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    call_count = 0

    class CancellingModel:
        def complete(self, messages, schemas, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ModelTurn({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "hello.txt"}),
                        },
                    }],
                }, "tool_calls", "fixture", "mock", None)
            raise model_module.ModelCallCancelled("Cancelled on turn 2")

    agent = CodingAgent(CancellingModel(), tools, session, max_steps=5)
    result = agent.run("Read file")
    assert result.status == "cancelled"
    assert result.tools_used == 1
    assert result.steps == 1
    assert session.load()[1] == "cancelled"


def test_agent_timeout_during_tool_execution_records_current_step(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "hello.txt").write_text("world", encoding="utf-8")
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    call_count = 0

    class StepModel:
        def complete(self, messages, schemas, timeout):
            nonlocal call_count
            call_count += 1
            return ModelTurn({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": f"call-{call_count}",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "hello.txt"}),
                    },
                }],
            }, "tool_calls", "fixture", "mock", None)

    original_execute = tools.execute

    def flaky_execute(name, args):
        if call_count == 2:
            raise TimeoutError("Tool command timed out")
        return original_execute(name, args)

    monkeypatch.setattr(tools, "execute", flaky_execute)

    agent = CodingAgent(StepModel(), tools, session, max_steps=5)
    result = agent.run("Read file")
    assert result.status == "timed_out"
    assert result.steps == 2
    assert session.load()[1] == "timed_out"
