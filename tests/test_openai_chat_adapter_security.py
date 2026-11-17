"""Reproduction tests for OpenAIChatAdapter and ModelTurn security hardening (SF-MODEL-02).

These tests explicitly capture baseline defects before hardening:
- Baseline OpenAIChatAdapter accepts non-callable cancelled parameter without TypeError.
- Baseline OpenAIChatAdapter crashes with AttributeError instead of TypeError when providers is not a list.
- Baseline OpenAIChatAdapter.complete accepts timeout=True (bool) and treats it as numeric 1.
- Baseline OpenAIChatAdapter.complete does not reject NaN or infinite timeouts at entry.
- Baseline OpenAIChatAdapter.complete crashes during iteration or treats caller invalid messages as provider errors.
- Baseline OpenAIChatAdapter.complete does not reject null bytes in messages at entry.
- Baseline OpenAIChatAdapter.complete does not validate tools schema at entry.
- Baseline OpenAIChatAdapter.complete accepts malformed tool_calls (e.g. non-list) from provider responses.
- Baseline OpenAIChatAdapter lacks reset() method to clear active_index.
- Baseline ModelTurn accepts invalid types (e.g. non-dict message, non-str finish_reason) without validation.
"""

from typing import Any

import pytest

from selfforge_agent.providers import ProviderConfig, TokenUsage
from selfforge_agent.runtime import model as model_module
from selfforge_agent.runtime.model import (
    ModelTurn,
    OpenAIChatAdapter,
)


def _make_provider(name: str = "mock-provider", model: str = "mock-model") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        label=name.title(),
        key=f"key-{name}",
        model=model,
        base_url=f"https://api.example.com/v1/{name}",
        status_url="",
    )


class TestAdapterInitAndCancelValidation:
    @pytest.mark.parametrize("invalid_providers", [None, "not-a-list", 12345, {"a": "b"}])
    def test_init_rejects_non_sequence_providers(self, invalid_providers: Any) -> None:
        with pytest.raises(TypeError, match="providers must be a sequence of ProviderConfig"):
            OpenAIChatAdapter(invalid_providers)

    def test_init_rejects_empty_providers_with_value_error(self) -> None:
        with pytest.raises(ValueError, match="Configure at least one model provider"):
            OpenAIChatAdapter([])

    @pytest.mark.parametrize("bad_element", [None, "invalid-config", 123, {}])
    def test_init_rejects_elements_not_provider_config(self, bad_element: Any) -> None:
        with pytest.raises(TypeError, match="elements in providers must be ProviderConfig instances"):
            OpenAIChatAdapter([bad_element])

    @pytest.mark.parametrize("bad_cancel", ["string", 123, [], {}])
    def test_init_rejects_non_callable_cancelled(self, bad_cancel: Any) -> None:
        with pytest.raises(TypeError, match="cancelled must be a callable"):
            OpenAIChatAdapter([_make_provider()], cancelled=bad_cancel)

    @pytest.mark.parametrize("bad_cancel", ["string", 123, [], {}])
    def test_set_cancel_check_rejects_non_callable(self, bad_cancel: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="cancelled must be a callable"):
            adapter.set_cancel_check(bad_cancel)

    def test_reset_method_restores_active_index_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p1 = _make_provider("p1")
        p2 = _make_provider("p2")
        adapter = OpenAIChatAdapter([p1, p2])

        # Force provider 1 to fail, advancing active_index to 1
        def fake_request(url, key, **kwargs):
            if "p1" in url:
                raise model_module.ProviderHTTPError("p1 failed", status_code=500)
            return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

        monkeypatch.setattr(model_module, "request_json", fake_request)
        turn = adapter.complete([{"role": "user", "content": "hi"}], [], 10)
        assert turn.provider == "p2"
        assert adapter.active_index == 1

        # Check reset() restores active_index
        assert hasattr(adapter, "reset"), "adapter should provide reset() method"
        adapter.reset()
        assert adapter.active_index == 0


class TestAdapterCompleteEntryValidation:
    @pytest.mark.parametrize("bad_timeout", [True, False, "10", None, [10], {"t": 10}])
    def test_complete_rejects_non_numeric_timeout(self, bad_timeout: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="timeout must be a real number"):
            adapter.complete([{"role": "user", "content": "hi"}], [], bad_timeout)

    @pytest.mark.parametrize("bad_timeout", [0, -1, -0.5, float("nan"), float("inf"), float("-inf"), 3601, 10000])
    def test_complete_rejects_non_positive_or_non_finite_or_excessive_timeout(self, bad_timeout: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(ValueError, match="timeout (must be a finite positive number|cannot exceed)"):
            adapter.complete([{"role": "user", "content": "hi"}], [], bad_timeout)

    @pytest.mark.parametrize("bad_messages", [None, "invalid-string", 123, {}])
    def test_complete_rejects_non_sequence_messages(self, bad_messages: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="messages must be a non-empty sequence"):
            adapter.complete(bad_messages, [], 10)

    def test_complete_rejects_empty_messages(self) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(ValueError, match="messages must contain at least one message"):
            adapter.complete([], [], 10)

    @pytest.mark.parametrize("bad_item", [None, "not-a-dict", 123, []])
    def test_complete_rejects_non_dict_message_elements(self, bad_item: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="each item in messages must be a dictionary"):
            adapter.complete([bad_item], [], 10)

    @pytest.mark.parametrize(
        "bad_message",
        [
            {},
            {"content": "hello"},
            {"role": 123, "content": "hello"},
            {"role": "", "content": "hello"},
        ],
    )
    def test_complete_rejects_missing_or_invalid_role(self, bad_message: dict) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(ValueError, match="message must contain a non-empty string 'role'"):
            adapter.complete([bad_message], [], 10)

    @pytest.mark.parametrize(
        "null_message",
        [
            {"role": "user\x00evil", "content": "hello"},
            {"role": "user", "content": "hello\x00evil"},
            {"role": "user", "content": "hello", "reasoning_content": "think\x00evil"},
        ],
    )
    def test_complete_rejects_null_bytes_in_messages(self, null_message: dict) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(ValueError, match="Null byte"):
            adapter.complete([null_message], [], 10)

    @pytest.mark.parametrize("bad_tools", ["not-a-list", 123, {}])
    def test_complete_rejects_non_sequence_tools(self, bad_tools: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="tools must be a sequence of tool definitions"):
            adapter.complete([{"role": "user", "content": "hi"}], bad_tools, 10)

    @pytest.mark.parametrize("bad_tool", [None, "tool-string", 123])
    def test_complete_rejects_non_dict_tool_elements(self, bad_tool: Any) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        with pytest.raises(TypeError, match="each tool definition must be a dictionary"):
            adapter.complete([{"role": "user", "content": "hi"}], [bad_tool], 10)


class TestProviderResponseValidation:
    def test_complete_rejects_empty_choices_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        monkeypatch.setattr(model_module, "request_json", lambda *a, **kw: {"choices": []})
        with pytest.raises(RuntimeError, match="All providers failed.*without valid choices"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)

    def test_complete_rejects_non_dict_choice_element(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        monkeypatch.setattr(model_module, "request_json", lambda *a, **kw: {"choices": ["invalid-choice"]})
        with pytest.raises(RuntimeError, match="All providers failed.*choice must be a dictionary"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)

    def test_complete_rejects_non_dict_message_in_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        monkeypatch.setattr(model_module, "request_json", lambda *a, **kw: {"choices": [{"message": "not-a-dict"}]})
        with pytest.raises(RuntimeError, match="All providers failed.*Invalid assistant message"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)

    def test_complete_rejects_non_list_tool_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        monkeypatch.setattr(
            model_module,
            "request_json",
            lambda *a, **kw: {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"content": None, "tool_calls": "malformed-string-not-list"},
                    }
                ]
            },
        )
        with pytest.raises(RuntimeError, match="All providers failed.*tool_calls must be a list"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)

    def test_complete_rejects_malformed_tool_call_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        monkeypatch.setattr(
            model_module,
            "request_json",
            lambda *a, **kw: {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"content": None, "tool_calls": ["not-a-dict-tool-call"]},
                    }
                ]
            },
        )
        with pytest.raises(RuntimeError, match="All providers failed.*tool_call element must be a dictionary"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)

    def test_complete_rejects_excessive_reasoning_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = OpenAIChatAdapter([_make_provider()])
        huge_reasoning = "x" * 1_000_001
        monkeypatch.setattr(
            model_module,
            "request_json",
            lambda *a, **kw: {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok", "reasoning_content": huge_reasoning},
                    }
                ]
            },
        )
        with pytest.raises(RuntimeError, match="All providers failed.*reasoning_content exceeds maximum limit"):
            adapter.complete([{"role": "user", "content": "hi"}], [], 10)


class TestModelTurnInvariants:
    def test_model_turn_rejects_non_dict_message(self) -> None:
        with pytest.raises(TypeError, match="ModelTurn message must be a dictionary"):
            ModelTurn(
                message="not-a-dict",  # type: ignore[arg-type]
                finish_reason="stop",
                provider="mock",
                model="mock",
                usage=None,
            )

    @pytest.mark.parametrize("bad_str_field", ["finish_reason", "provider", "model"])
    def test_model_turn_rejects_non_str_metadata_fields(self, bad_str_field: str) -> None:
        kwargs: dict[str, Any] = {
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
            "provider": "mock",
            "model": "mock",
            "usage": None,
        }
        kwargs[bad_str_field] = 12345
        with pytest.raises(TypeError, match=f"ModelTurn {bad_str_field} must be a string"):
            ModelTurn(**kwargs)

    def test_model_turn_rejects_invalid_usage_type(self) -> None:
        with pytest.raises(TypeError, match="ModelTurn usage must be TokenUsage or None"):
            ModelTurn(
                message={"role": "assistant", "content": "ok"},
                finish_reason="stop",
                provider="mock",
                model="mock",
                usage="invalid-usage-type",  # type: ignore[arg-type]
            )

    def test_model_turn_valid_instantiation(self) -> None:
        turn = ModelTurn(
            message={"role": "assistant", "content": "hello world"},
            finish_reason="stop",
            provider="openai",
            model="gpt-4o",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
        assert turn.finish_reason == "stop"
        assert turn.provider == "openai"
        assert turn.usage is not None and turn.usage.total_tokens == 30


def test_long_turn_budget_respects_transport_timeout_ceiling(monkeypatch):
    import io
    import json

    from selfforge_agent.integrations import model_http

    received = []

    def open_fixture(request, timeout):
        received.append(timeout)
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'fixture'}}]}).encode())

    monkeypatch.setattr(model_http, 'urlopen', open_fixture)
    turn = OpenAIChatAdapter([_make_provider()]).complete([{'role': 'user', 'content': 'fixture'}], [], 600)
    assert turn.message['content'] == 'fixture'
    assert len(received) == 1
    assert 0 < received[0] <= 300


@pytest.mark.parametrize('timeout', [10**1000, 0.0001], ids=['huge_integer', 'below_minimum'])
def test_timeout_validation_is_controlled_and_matches_bounds(monkeypatch, timeout):
    monkeypatch.setattr(model_module, 'request_json', lambda *_a, **_k: {'choices': [{'message': {'content': 'fixture'}}]})
    with pytest.raises(ValueError):
        OpenAIChatAdapter([_make_provider()]).complete([{'role': 'user'}], [], timeout)


@pytest.mark.parametrize('tool', [{}, {'function': None}, {'function': {}}, {'function': {'name': ''}},
                                 {'function': {'name': 17}}, {'function': {'name': 'bad name'}},
                                 {'type': 'other', 'function': {'name': 'read_file'}}])
def test_tool_definitions_are_validated_before_any_provider_call(monkeypatch, tool):
    calls = []
    monkeypatch.setattr(model_module, 'request_json', lambda *_a, **_k: calls.append(1) or {'choices': [{'message': {'content': 'fixture'}}]})
    with pytest.raises((TypeError, ValueError)):
        OpenAIChatAdapter([_make_provider()]).complete([{'role': 'user'}], [tool], 10)
    assert calls == []


@pytest.mark.parametrize('content', [{'text': 'not a string'}, [1], 7, True])
def test_non_text_provider_content_cannot_become_a_final_answer(monkeypatch, content):
    monkeypatch.setattr(model_module, 'request_json', lambda *_a, **_k: {'choices': [{'message': {'content': content}}]})
    with pytest.raises(RuntimeError):
        OpenAIChatAdapter([_make_provider()]).complete([{'role': 'user'}], [], 10)


@pytest.mark.parametrize('finish_reason', [0, True, {'stop': True}])
def test_provider_finish_reason_type_is_not_silently_coerced(monkeypatch, finish_reason):
    monkeypatch.setattr(model_module, 'request_json', lambda *_a, **_k: {
        'choices': [{'message': {'content': 'fixture'}, 'finish_reason': finish_reason}]})
    with pytest.raises(RuntimeError):
        OpenAIChatAdapter([_make_provider()]).complete([{'role': 'user'}], [], 10)


def test_agent_total_budget_can_exceed_a_single_model_turn(tmp_path, monkeypatch):
    from selfforge_agent.runtime.agent import CodingAgent
    from selfforge_agent.runtime.session import SessionStore
    from selfforge_agent.runtime.tools import ToolRegistry

    calls = []
    monkeypatch.setattr(model_module, 'request_json', lambda *_a, **kw: calls.append(kw['timeout']) or {
        'choices': [{'message': {'content': 'fixture'}, 'finish_reason': 'stop'}]})
    adapter = OpenAIChatAdapter([_make_provider()])
    result = CodingAgent(adapter, ToolRegistry(tmp_path), SessionStore(tmp_path), time_budget=7200).run('inspect fixture')
    assert result.status == 'unverified'
    assert len(calls) == 1
    assert 0 < calls[0] <= 300
