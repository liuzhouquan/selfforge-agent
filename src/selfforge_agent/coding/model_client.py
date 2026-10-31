"""Small OpenAI-compatible chat adapter; model output is validated before any edit."""

from typing import Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from selfforge_agent.coding.models import CodeEdit, RepairObservation
from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.providers import (
    ModelAttempt,
    ProviderConfig,
    TokenUsage,
    token_usage_from_response,
)


class ModelError(ValueError):
    """The model request or structured response failed."""


class CodingModel(Protocol):
    def propose(self, observation: RepairObservation) -> CodeEdit: ...


class OpenAICompatibleCodingModel:
    def __init__(self, *, model: str, base_url: str, api_key: str = "", timeout: float = 45) -> None:
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model base URL must not contain credentials, query, or fragment")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Model base URL must use HTTPS or local HTTP")
        if not model.strip() or not parsed.hostname or timeout <= 0:
            raise ValueError("Model name, base URL, and positive timeout are required")
        if not api_key and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("An API key is required for a remote model endpoint")
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.timeout = timeout
        self.last_usage: TokenUsage | None = None
        self.usage_history: list[TokenUsage | None] = []

    def propose(self, observation: RepairObservation) -> CodeEdit:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a coding agent repairing a regression. Return one JSON object with "
                        "path, old_text, new_text, rationale. Replace an exact unique substring in "
                        "one supplied source file. Do not edit tests or config. Use the observed test "
                        "failure as evidence; do not claim success. No Markdown."
                    ),
                },
                {"role": "user", "content": observation.model_dump_json()},
            ],
            "response_format": {"type": "json_object"},
        }
        self.last_usage = None
        try:
            response = request_json(self.url, self.api_key, payload=payload, timeout=self.timeout)
            self.last_usage = token_usage_from_response(response)
            content = response["choices"][0]["message"]["content"]
            edit = CodeEdit.model_validate_json(content)
            return edit
        except ProviderHTTPError as exc:
            raise ModelError(str(exc)) from exc
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelError("Model did not return a valid CodeEdit JSON object") from exc
        finally:
            self.usage_history.append(self.last_usage)


class FallbackCodingModel:
    """Try configured providers in order; stop retrying one after a request failure."""

    def __init__(self, providers: list[ProviderConfig], *, timeout: float = 25) -> None:
        if not providers:
            raise ValueError("At least one configured model provider is required")
        self.providers = providers
        self.timeout = timeout
        self.active_index = 0
        self.attempts: list[ModelAttempt] = []
        self.selected_provider: str | None = None

    def propose(self, observation: RepairObservation) -> CodeEdit:
        failures = []
        for index in range(self.active_index, len(self.providers)):
            provider = self.providers[index]
            client = OpenAICompatibleCodingModel(
                model=provider.model, base_url=provider.base_url,
                api_key=provider.key, timeout=self.timeout,
            )
            try:
                edit = client.propose(observation)
            except ModelError as exc:
                failures.append(f"{provider.name}: {exc}")
                self.attempts.append(ModelAttempt(
                    provider=provider.name, model=provider.model, status="error",
                    error=str(exc), usage=client.last_usage,
                ))
                self.active_index = index + 1
                continue
            self.active_index = index
            self.selected_provider = provider.name
            self.attempts.append(ModelAttempt(
                provider=provider.name, model=provider.model, status="success", usage=client.last_usage
            ))
            return edit
        raise ModelError("All model providers failed: " + "; ".join(failures))
