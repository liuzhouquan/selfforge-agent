"""Security and robustness tests for CodingAgent lifecycle and boundaries (SF-AGENT-01)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


class DummyModel:
    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        return ModelTurn(
            {"role": "assistant", "content": "Done."},
            "stop",
            "fixture",
            "dummy",
            None,
        )


def _make_agent(
    tmp_path: Path,
    *,
    model=None,
    tools=None,
    session=None,
    max_steps=12,
    time_budget=300,
    context_budget=8000,
    cancel=None,
    approval_hook=None,
) -> CodingAgent:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    m = model or DummyModel()
    t = tools or ToolRegistry(root)
    s = session or SessionStore(tmp_path / "sessions")
    return CodingAgent(
        m,
        t,
        s,
        max_steps=max_steps,
        time_budget=time_budget,
        context_budget=context_budget,
        cancel=cancel,
        approval_hook=approval_hook,
    )


# ---------------------------------------------------------------------------
# SF-AGENT-01-A1: __init__ parameter validation and type defense
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_steps",
    [True, False, 12.5, float("nan"), float("inf"), 0, -1, 101, "12", None],
)
def test_coding_agent_init_rejects_invalid_max_steps(tmp_path: Path, bad_steps) -> None:
    with pytest.raises((ValueError, TypeError)):
        _make_agent(tmp_path, max_steps=bad_steps)


@pytest.mark.parametrize(
    "bad_budget",
    [True, False, 0, -10, float("nan"), float("inf"), "300", None],
)
def test_coding_agent_init_rejects_invalid_time_budget(tmp_path: Path, bad_budget) -> None:
    with pytest.raises((ValueError, TypeError)):
        _make_agent(tmp_path, time_budget=bad_budget)


@pytest.mark.parametrize(
    "bad_context",
    [True, False, 499, 0, -100, float("nan"), "8000", None],
)
def test_coding_agent_init_rejects_invalid_context_budget(tmp_path: Path, bad_context) -> None:
    with pytest.raises((ValueError, TypeError)):
        _make_agent(tmp_path, context_budget=bad_context)


def test_coding_agent_init_rejects_invalid_cancel_and_approval_hook(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="cancel"):
        _make_agent(tmp_path, cancel="not_an_event")

    with pytest.raises(TypeError, match="approval_hook"):
        _make_agent(tmp_path, approval_hook="not_callable")


# ---------------------------------------------------------------------------
# SF-AGENT-01-A2: _resume_pending malformed messages and tool_calls defense
# ---------------------------------------------------------------------------


def test_resume_pending_handles_non_dict_and_malformed_messages(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    # messages containing non-dict items
    messages = [
        "not a dict",
        None,
        123,
        {"role": "system", "content": "You are agent"},
        {"role": "user", "content": "do task"},
        {
            "role": "assistant",
            "tool_calls": [
                "not a dict call",
                None,
                {"id": "", "function": {"name": "read_file"}},
                {"id": "call_1", "function": "not_a_dict_func"},
                {"id": "call_2", "function": {"name": "edit_file"}},
            ],
        },
    ]
    # Should not raise AttributeError or TypeError
    agent._resume_pending(messages)
    # The valid tool call call_2 should receive synthetic tool reply
    replies = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    call_ids = {r.get("tool_call_id") for r in replies}
    assert "call_2" in call_ids


def test_resume_pending_handles_non_list_tool_calls(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "tool_calls": "string_not_list"},
    ]
    agent._resume_pending(messages)
    assert len(messages) == 2


# ---------------------------------------------------------------------------
# SF-AGENT-01-A3: run task input defense and approval_hook exception isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_task", [None, 123, ["task"], {"task": "do"}])
def test_run_rejects_non_string_task(tmp_path: Path, bad_task) -> None:
    agent = _make_agent(tmp_path)
    with pytest.raises(TypeError, match="task"):
        agent.run(bad_task)  # type: ignore[arg-type]


def test_run_rejects_oversized_task(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    huge_task = "x" * 100_001
    with pytest.raises(ValueError, match="size limit"):
        agent.run(huge_task)


def test_approval_hook_exception_is_isolated_and_reported(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "calculator.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    class CrashingHookModel:
        def __init__(self) -> None:
            self.turn = 0

        def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
            self.turn += 1
            if self.turn == 1:
                return ModelTurn(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_crash",
                                "type": "function",
                                "function": {
                                    "name": "edit_file",
                                    "arguments": '{"path": "calculator.py", "old_text": "1", "new_text": "2"}',
                                },
                            }
                        ],
                    },
                    "tool_calls",
                    "fixture",
                    "scripted",
                    None,
                )
            return ModelTurn(
                {"role": "assistant", "content": "Noted rejection."},
                "stop",
                "fixture",
                "scripted",
                None,
            )

    def crashing_hook(name: str, arguments: dict) -> bool:
        raise RuntimeError("Hook crashed unexpectedly")

    session = SessionStore(tmp_path / "sessions")
    tools = ToolRegistry(root)
    agent = CodingAgent(CrashingHookModel(), tools, session, approval_hook=crashing_hook)
    result = agent.run("Edit file")

    # The agent run should NOT crash with RuntimeError. It should finish normally.
    assert result.status == "unverified"
    messages, _ = session.load()
    tool_replies = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_replies) == 1
    assert "approval hook error" in tool_replies[0]["content"]
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_run_handles_malformed_tool_call_structure_and_json(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)

    class MalformedToolModel:
        def __init__(self) -> None:
            self.turn = 0

        def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
            self.turn += 1
            if self.turn == 1:
                return ModelTurn(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            "not_a_dict_call",
                            {"id": "call_bad_func", "function": "not_a_dict"},
                            {
                                "id": "call_bad_json",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{malformed json"},
                            },
                        ],
                    },
                    "tool_calls",
                    "fixture",
                    "scripted",
                    None,
                )
            return ModelTurn(
                {"role": "assistant", "content": "Done."},
                "stop",
                "fixture",
                "scripted",
                None,
            )

    session = SessionStore(tmp_path / "sessions")
    tools = ToolRegistry(root)
    agent = CodingAgent(MalformedToolModel(), tools, session)
    result = agent.run("Test malformed calls")

    assert result.status == "unverified"
    messages, _ = session.load()
    tool_replies = [m for m in messages if m.get("role") == "tool"]
    # The malformed function and malformed JSON calls receive tool replies
    assert len(tool_replies) == 2


# ---------------------------------------------------------------------------
# SF-AGENT-01-A4: Telemetry state alignment and robust finish
# ---------------------------------------------------------------------------


def test_finish_state_and_metrics_alignment(tmp_path: Path) -> None:
    session = SessionStore(tmp_path / "sessions")
    # Mock token_metrics returning malformed cost
    session.token_metrics = MagicMock(
        return_value={"total_tokens": -5, "cost": {"invalid": "shape"}}
    )

    agent = _make_agent(tmp_path, session=session)
    result = agent._finish("completed", "answer", steps=2, tools_used=3)

    assert result.status == "completed"
    assert result.steps == 2
    assert result.tools_used == 3
    assert result.total_tokens == 0  # Negative tokens sanitized to 0
    assert result.monetary_cost is None  # Malformed cost does not crash


def test_cancel_before_and_during_run_aligns_session_status(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    session = SessionStore(tmp_path / "sessions")
    agent = _make_agent(tmp_path, session=session, cancel=cancel)

    result = agent.run("Cancelled task")
    assert result.status == "cancelled"
    assert result.steps == 0

    # Session record matches result
    _messages, status = session.load()
    assert status == "cancelled"


@pytest.mark.parametrize("metrics", [None, [], True])
def test_public_run_handles_non_mapping_token_metrics(tmp_path, metrics):
    agent = _make_agent(tmp_path)
    agent.session.token_metrics = lambda: metrics
    result = agent.run("inspect fixture")
    assert result.status == "unverified"
    assert result.monetary_cost is None
    assert result.total_tokens == 0
    assert agent.session.load()[1] == result.status


@pytest.mark.parametrize("bad_field", ["tool_call_id", "name"])
def test_resume_filters_unhashable_history_identifiers(tmp_path, bad_field):
    agent = _make_agent(tmp_path)
    agent.session.record_message({"role": "system", "content": "fixture"})
    agent.session.record_message({"role": "user", "content": "inspect"})
    name = [] if bad_field == "name" else "read_file"
    agent.session.record_message({"role": "assistant", "tool_calls": [
        {"id": "pending", "function": {"name": name, "arguments": "{}"}}]})
    if bad_field == "tool_call_id":
        agent.session.record_message({"role": "tool", "tool_call_id": [], "content": "invalid"})
    result = agent.run(resume=True)
    assert result.status == "unverified"
    assert agent.session.load()[1] == result.status


def test_cancellation_predicate_must_be_callable(tmp_path):
    class InvalidCancel:
        is_set = True

    with pytest.raises(TypeError, match="cancel"):
        _make_agent(tmp_path, cancel=InvalidCancel())


def test_unrepresentable_time_budget_is_controlled(tmp_path):
    with pytest.raises(ValueError):
        _make_agent(tmp_path, time_budget=10**1000)


def test_missing_call_id_is_repaired_in_both_native_messages(tmp_path):
    class Model:
        turns = 0

        def complete(self, messages, tools, timeout):
            self.turns += 1
            if self.turns == 1:
                return ModelTurn({"role": "assistant", "tool_calls": [
                    {"function": {"name": "read_file", "arguments": '{"path":"fixture.py"}'}}]},
                    "tool_calls", "fixture", "fixture", None)
            request = next(m for m in reversed(messages) if m.get("role") == "assistant")
            reply = messages[-1]
            assert isinstance(request['tool_calls'][0].get('id'), str)
            assert request['tool_calls'][0]['id'] == reply['tool_call_id']
            return ModelTurn({"role": "assistant", "content": "Inspected"}, "stop", "fixture", "fixture", None)

    agent = _make_agent(tmp_path, model=Model())
    (agent.tools.root / "fixture.py").write_text("value = 1\n")
    assert agent.run("inspect").status == "unverified"


@pytest.mark.parametrize('malformed', ['function', 'name', 'arguments', 'type'])
def test_native_adapter_never_resends_malformed_tool_envelopes(tmp_path, monkeypatch, malformed):
    import json

    from selfforge_agent.integrations.model_http import ProviderHTTPError
    from selfforge_agent.providers import ProviderConfig
    from selfforge_agent.runtime.model import OpenAIChatAdapter

    requests = []
    call = {'id': 'bad', 'type': 'function', 'function': {
        'name': 'edit_file', 'arguments': json.dumps({'path': 'fixture.py', 'old_text': '1', 'new_text': '2'})}}
    if malformed == 'function':
        call['function'] = None
    elif malformed == 'name':
        call['function']['name'] = []
    elif malformed == 'arguments':
        call['function']['arguments'] = {'path': 'fixture.py', 'old_text': '1', 'new_text': '2'}
    else:
        call['type'] = 'invalid'

    def response(_url, _key, *, payload, **_kwargs):
        requests.append(payload)
        if len(requests) == 1:
            return {'choices': [{'message': {'tool_calls': [call]}, 'finish_reason': 'tool_calls'}]}
        for message in payload['messages']:
            for request in message.get('tool_calls', []):
                function = request.get('function')
                if (request.get('type') != 'function' or not isinstance(function, dict)
                        or not isinstance(function.get('name'), str)
                        or not isinstance(function.get('arguments'), str)):
                    raise ProviderHTTPError('fixture native protocol validation failed')
        return {'choices': [{'message': {'content': 'Handled invalid tool'}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr('selfforge_agent.runtime.model.request_json', response)
    model = OpenAIChatAdapter([ProviderConfig(name='deepseek', label='fixture', key='', status_url='', model='fixture', base_url='http://localhost:1234')])
    agent = _make_agent(tmp_path, model=model)
    target = agent.tools.root / 'fixture.py'
    target.write_text('VALUE = 1\n')
    result = agent.run('Inspect fixture')
    assert result.status == 'unverified'
    assert target.read_text() == 'VALUE = 1\n'
    assert len(requests) == 2
    messages, _ = agent.session.load()
    replies = [m for m in messages if m.get('role') == 'tool']
    assert len(replies) == 1
    assert 'malformed' in replies[0]['content'] or 'JSON string' in replies[0]['content']
