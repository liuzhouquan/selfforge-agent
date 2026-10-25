"""OpenAI-compatible native tool-calling adapter with provider fallback."""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.providers import (
    ProviderConfig,
    TokenUsage,
    token_usage_from_response,
    validate_base_url,
)


@dataclass(frozen=True)
class ModelTurn:
    message: dict
    finish_reason: str
    provider: str
    model: str
    usage: TokenUsage | None


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn: ...


class OpenAIChatAdapter:
    def __init__(self, providers: list[ProviderConfig]) -> None:
        if not providers:
            raise ValueError("Configure at least one model provider")
        for provider in providers:
            validate_base_url(provider.base_url)
            if not provider.key and urlsplit(provider.base_url).hostname not in {
                "localhost", "127.0.0.1", "::1"
            }:
                raise ValueError(f"API key required for {provider.name}")
        self.providers = providers
        self.active_index = 0

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        errors = []
        for index in range(self.active_index, len(self.providers)):
            provider = self.providers[index]
            try:
                # DeepSeek thinking-mode tool turns require their original reasoning_content.
                # Other OpenAI-compatible providers may reject this extension.
                history = (messages if provider.name == "deepseek" else [
                    {key: value for key, value in message.items() if key != "reasoning_content"}
                    for message in messages
                ])
                payload = {"model": provider.model, "messages": history}
                if tools:
                    payload.update({"tools": tools, "tool_choice": "auto"})
                response = request_json(provider.base_url.rstrip("/") + "/chat/completions",
                                        provider.key, payload=payload, timeout=timeout,
                                        max_bytes=2_000_000)
                choice = response["choices"][0]
                raw = choice["message"]
                if not isinstance(raw, dict):
                    raise TypeError("Invalid assistant message")
                message = {"role": "assistant", "content": raw.get("content") or ""}
                if "reasoning_content" in raw:
                    reasoning = raw["reasoning_content"]
                    if reasoning is not None and not isinstance(reasoning, str):
                        raise TypeError("Invalid reasoning_content")
                    message["reasoning_content"] = reasoning
                if raw.get("tool_calls"):
                    message["tool_calls"] = raw["tool_calls"]
                self.active_index = index
                return ModelTurn(message, choice.get("finish_reason") or "stop",
                                 provider.name, provider.model, token_usage_from_response(response))
            except (ProviderHTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(f"{provider.name}: {exc}")
                self.active_index = index + 1
        raise RuntimeError("All providers failed: " + "; ".join(errors))
