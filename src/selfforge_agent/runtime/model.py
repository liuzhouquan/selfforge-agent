"""OpenAI-compatible native tool-calling adapter with provider fallback."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlsplit

from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.providers import (
    ProviderConfig,
    TokenUsage,
    token_usage_from_response,
    validate_base_url,
)

MAX_MODEL_TIMEOUT = 3600.0
MIN_MODEL_TIMEOUT = 0.001
MAX_MESSAGES_COUNT = 10_000
MAX_TOOLS_COUNT = 256
MAX_TOOL_NAME_LEN = 64
MAX_REASONING_LEN = 1_000_000
MAX_PROVIDERS_COUNT = 50


@dataclass(frozen=True)
class ModelTurn:
    message: dict
    finish_reason: str
    provider: str
    model: str
    usage: TokenUsage | None

    def __post_init__(self) -> None:
        if not isinstance(self.message, dict):
            raise TypeError(f"ModelTurn message must be a dictionary, got {type(self.message).__name__}")
        if not isinstance(self.finish_reason, str):
            raise TypeError(f"ModelTurn finish_reason must be a string, got {type(self.finish_reason).__name__}")
        if not isinstance(self.provider, str):
            raise TypeError(f"ModelTurn provider must be a string, got {type(self.provider).__name__}")
        if not isinstance(self.model, str):
            raise TypeError(f"ModelTurn model must be a string, got {type(self.model).__name__}")
        if self.usage is not None and not isinstance(self.usage, TokenUsage):
            raise TypeError(f"ModelTurn usage must be TokenUsage or None, got {type(self.usage).__name__}")


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn: ...


class ModelCallTimedOut(TimeoutError):
    """The shared deadline for one model call has expired."""


class ModelCallCancelled(RuntimeError):
    """The caller cancelled a model call between provider attempts."""


def _check_no_null_bytes(val: Any, field_name: str) -> None:
    if isinstance(val, str) and "\x00" in val:
        raise ValueError(f"Null byte detected in {field_name}")


class OpenAIChatAdapter:
    def __init__(
        self,
        providers: Sequence[ProviderConfig],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if not isinstance(providers, (list, tuple)):
            raise TypeError(f"providers must be a sequence of ProviderConfig, got {type(providers).__name__}")
        if not providers:
            raise ValueError("Configure at least one model provider")
        if len(providers) > MAX_PROVIDERS_COUNT:
            raise ValueError(f"providers count cannot exceed {MAX_PROVIDERS_COUNT}")

        for idx, provider in enumerate(providers):
            if not isinstance(provider, ProviderConfig):
                raise TypeError(
                    f"elements in providers must be ProviderConfig instances, got {type(provider).__name__} at index {idx}"
                )
            validate_base_url(provider.base_url)
            _check_no_null_bytes(provider.name, "provider.name")
            _check_no_null_bytes(provider.model, "provider.model")
            if not provider.key and urlsplit(provider.base_url).hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError(f"API key required for {provider.name}")

        if cancelled is not None and not callable(cancelled):
            raise TypeError("cancelled must be a callable")

        self.providers = list(providers)
        self.active_index = 0
        self.cancelled = cancelled

    def set_cancel_check(self, cancelled: Callable[[], bool]) -> None:
        if cancelled is not None and not callable(cancelled):
            raise TypeError("cancelled must be a callable")
        self.cancelled = cancelled

    def reset(self) -> None:
        """Reset the active fallback provider index to the first configured provider."""
        self.active_index = 0

    def _remaining(self, deadline: float) -> float:
        if self.cancelled is not None and self.cancelled():
            raise ModelCallCancelled("Model call cancelled")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ModelCallTimedOut("Model call timed out during provider fallback")
        return remaining

    def complete(self, messages: Sequence[dict], tools: Sequence[dict], timeout: float) -> ModelTurn:
        # 1. Entry validation for timeout
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise TypeError(f"timeout must be a real number, got {type(timeout).__name__}")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"timeout must be a finite positive number, got {timeout}")
        if timeout > MAX_MODEL_TIMEOUT:
            raise ValueError(f"timeout cannot exceed {MAX_MODEL_TIMEOUT} seconds, got {timeout}")

        # 2. Entry validation for messages
        if not isinstance(messages, (list, tuple)):
            raise TypeError(f"messages must be a non-empty sequence, got {type(messages).__name__}")
        if not messages:
            raise ValueError("messages must contain at least one message")
        if len(messages) > MAX_MESSAGES_COUNT:
            raise ValueError(f"messages count cannot exceed {MAX_MESSAGES_COUNT}")

        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TypeError(f"each item in messages must be a dictionary, got {type(msg).__name__} at index {idx}")
            role = msg.get("role")
            if not isinstance(role, str) or not role.strip():
                raise ValueError(f"message must contain a non-empty string 'role' at index {idx}")
            _check_no_null_bytes(role, f"messages[{idx}].role")
            content = msg.get("content")
            if content is not None:
                _check_no_null_bytes(content, f"messages[{idx}].content")
            reasoning = msg.get("reasoning_content")
            if reasoning is not None:
                _check_no_null_bytes(reasoning, f"messages[{idx}].reasoning_content")

        # 3. Entry validation for tools
        if tools is not None:
            if not isinstance(tools, (list, tuple)):
                raise TypeError(f"tools must be a sequence of tool definitions, got {type(tools).__name__}")
            if len(tools) > MAX_TOOLS_COUNT:
                raise ValueError(f"tools count cannot exceed {MAX_TOOLS_COUNT}")
            for idx, tool in enumerate(tools):
                if not isinstance(tool, dict):
                    raise TypeError(f"each tool definition must be a dictionary, got {type(tool).__name__} at index {idx}")
                fn = tool.get("function")
                if isinstance(fn, dict):
                    name = fn.get("name")
                    if isinstance(name, str):
                        _check_no_null_bytes(name, f"tools[{idx}].function.name")
                        if len(name) > MAX_TOOL_NAME_LEN:
                            raise ValueError(f"tool function name exceeds {MAX_TOOL_NAME_LEN} characters at index {idx}")

        deadline = monotonic() + timeout
        errors: list[str] = []

        if self.active_index >= len(self.providers):
            raise RuntimeError("All providers failed: all configured providers have been exhausted")

        for index in range(self.active_index, len(self.providers)):
            provider = self.providers[index]
            try:
                remaining = self._remaining(deadline)
                # DeepSeek thinking-mode tool turns require their original reasoning_content.
                # Other OpenAI-compatible providers may reject this extension.
                history = (
                    list(messages)
                    if provider.name == "deepseek"
                    else [
                        {key: value for key, value in message.items() if key != "reasoning_content"}
                        for message in messages
                    ]
                )
                payload: dict[str, Any] = {"model": provider.model, "messages": history}
                if tools:
                    payload.update({"tools": list(tools), "tool_choice": "auto"})

                response = request_json(
                    provider.base_url.rstrip("/") + "/chat/completions",
                    provider.key,
                    payload=payload,
                    timeout=remaining,
                    max_bytes=2_000_000,
                )
                self._remaining(deadline)

                if not isinstance(response, dict):
                    raise TypeError(f"Provider {provider.name} returned non-dictionary response")
                choices = response.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError(f"Provider {provider.name} returned response without valid choices")

                choice = choices[0]
                if not isinstance(choice, dict):
                    raise TypeError(f"Provider {provider.name} choice must be a dictionary")

                raw = choice.get("message")
                if not isinstance(raw, dict):
                    raise TypeError("Invalid assistant message")

                message_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": raw.get("content") or "",
                }

                if "reasoning_content" in raw:
                    reasoning_val = raw["reasoning_content"]
                    if reasoning_val is not None:
                        if not isinstance(reasoning_val, str):
                            raise TypeError("Invalid reasoning_content")
                        if len(reasoning_val) > MAX_REASONING_LEN:
                            raise ValueError(
                                f"reasoning_content exceeds maximum limit ({MAX_REASONING_LEN} chars)"
                            )
                        message_dict["reasoning_content"] = reasoning_val

                if raw.get("tool_calls") is not None:
                    tool_calls = raw["tool_calls"]
                    if not isinstance(tool_calls, list):
                        raise TypeError("tool_calls must be a list")
                    for tc_idx, tc_item in enumerate(tool_calls):
                        if not isinstance(tc_item, dict):
                            raise TypeError(f"tool_call element must be a dictionary at index {tc_idx}")
                    message_dict["tool_calls"] = tool_calls

                self.active_index = index
                finish_reason = str(choice.get("finish_reason") or "stop")
                return ModelTurn(
                    message_dict,
                    finish_reason,
                    provider.name,
                    provider.model,
                    token_usage_from_response(response),
                )
            except (ProviderHTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(f"{provider.name}: {exc}")
                self.active_index = index + 1
            self._remaining(deadline)

        raise RuntimeError("All providers failed: " + "; ".join(errors))
