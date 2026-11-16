"""Small OpenAI-compatible chat adapter; model output is validated before any edit."""

import math
import os
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from selfforge_agent.coding.models import CodeEdit, RepairObservation
from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.providers import (
    ModelAttempt,
    ProviderConfig,
    TokenUsage,
    json_object_options,
    token_usage_from_response,
    validate_base_url,
)

MAX_OBSERVATION_PAYLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_PATH_CHARS = 1024
MAX_TEXT_CHARS = 1_000_000
MAX_RATIONALE_CHARS = 100_000


class ModelError(ValueError):
    """The model request or structured response failed."""


class CodingModel(Protocol):
    def propose(self, observation: RepairObservation) -> CodeEdit: ...


def _validate_finite_positive_timeout(timeout: object) -> float:
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise TypeError("Timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be a finite positive number")
    return float(timeout)


def _extract_json_payload(raw: str) -> str:
    """Extract JSON payload from raw model text, stripping markdown code fences if present."""
    trimmed = raw.strip()
    if trimmed.startswith("{"):
        return trimmed
    if "```" in trimmed:
        fence_start = trimmed.find("```")
        content_start = trimmed.find("\n", fence_start)
        fence_end = trimmed.rfind("```")
        if content_start != -1 and fence_end > content_start:
            return trimmed[content_start + 1 : fence_end].strip()
        if trimmed.startswith("```") and trimmed.endswith("```") and len(trimmed) >= 6:
            inner = trimmed[3:-3].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            return inner
    return trimmed


def _validate_code_edit(edit: CodeEdit) -> CodeEdit:
    """Validate safety boundaries and path constraints on proposed code edit."""
    if "\0" in edit.path:
        raise ModelError("Edit path must not contain null bytes")
    if os.path.isabs(edit.path) or edit.path.startswith("/"):
        raise ModelError("Edit path must be a relative path")
    norm = os.path.normpath(edit.path)
    if norm in {".", ".."} or ".." in edit.path.split("/"):
        raise ModelError("Edit path must not contain directory traversal")
    if len(edit.path) > MAX_PATH_CHARS:
        raise ModelError(f"Edit path exceeds maximum length of {MAX_PATH_CHARS} characters")
    if len(edit.old_text) > MAX_TEXT_CHARS:
        raise ModelError(f"Edit old_text exceeds maximum length of {MAX_TEXT_CHARS} characters")
    if len(edit.new_text) > MAX_TEXT_CHARS:
        raise ModelError(f"Edit new_text exceeds maximum length of {MAX_TEXT_CHARS} characters")
    if len(edit.rationale) > MAX_RATIONALE_CHARS:
        raise ModelError(f"Edit rationale exceeds maximum length of {MAX_RATIONALE_CHARS} characters")
    return edit


class OpenAICompatibleCodingModel:
    def __init__(self, *, model: str, base_url: str, api_key: str = "", timeout: float = 45) -> None:
        if not isinstance(model, str):
            raise TypeError("Model name must be a string")
        if not model.strip():
            raise ValueError("Model name must be a non-empty string")
        if not isinstance(base_url, str):
            raise TypeError("Base URL must be a string")
        if not base_url.strip():
            raise ValueError("Base URL must be a non-empty string")
        if not isinstance(api_key, str):
            raise TypeError("API key must be a string")
        self.timeout = _validate_finite_positive_timeout(timeout)

        base_url = validate_base_url(base_url)
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model base URL must not contain credentials, query, or fragment")
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("Model base URL must use HTTPS or local HTTP")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Model base URL must use HTTPS or local HTTP")
        if not parsed.hostname or not parsed.netloc:
            raise ValueError("Model base URL must contain a valid host")
        if not api_key.strip() and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("An API key is required for a remote model endpoint")

        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.last_usage: TokenUsage | None = None
        self.usage_history: list[TokenUsage | None] = []

    def propose(self, observation: RepairObservation) -> CodeEdit:
        if not isinstance(observation, RepairObservation):
            raise TypeError("Observation must be an instance of RepairObservation")

        observation_json = observation.model_dump_json()
        if len(observation_json.encode("utf-8")) > MAX_OBSERVATION_PAYLOAD_BYTES:
            raise ModelError(
                f"Observation payload exceeds maximum size limit ({MAX_OBSERVATION_PAYLOAD_BYTES} bytes)"
            )

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
                {"role": "user", "content": observation_json},
            ],
            **json_object_options(self.url),
        }
        self.last_usage = None
        try:
            response = request_json(self.url, self.api_key, payload=payload, timeout=self.timeout)
            if not isinstance(response, dict):
                raise ModelError("Provider response is not a JSON object")
            self.last_usage = token_usage_from_response(response)
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ModelError("Provider response missing choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ModelError("Provider response choice is not an object")
            if choice.get("finish_reason") == "length":
                raise ModelError("Model response was truncated due to length limit")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ModelError("Provider response choice missing message object")
            content = message.get("content")
            if not isinstance(content, str):
                raise ModelError("Provider response message content must be a string")

            clean_content = _extract_json_payload(content)
            edit = CodeEdit.model_validate_json(clean_content)
            return _validate_code_edit(edit)
        except ProviderHTTPError as exc:
            raise ModelError(str(exc)) from exc
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, ModelError):
                raise
            raise ModelError("Model did not return a valid CodeEdit JSON object") from exc
        finally:
            self.usage_history.append(self.last_usage)


class FallbackCodingModel:
    """Try configured providers in order; stop retrying one after a request failure."""

    def __init__(self, providers: list[ProviderConfig], *, timeout: float = 25) -> None:
        if not isinstance(providers, list):
            raise TypeError("Providers must be a list of ProviderConfig instances")
        if not providers:
            raise ValueError("At least one configured model provider is required")
        for p in providers:
            if not isinstance(p, ProviderConfig):
                raise TypeError("Each provider must be an instance of ProviderConfig")
        self.timeout = _validate_finite_positive_timeout(timeout)
        self.providers = providers
        self.active_index = 0
        self.attempts: list[ModelAttempt] = []
        self.selected_provider: str | None = None

    def propose(self, observation: RepairObservation) -> CodeEdit:
        if not isinstance(observation, RepairObservation):
            raise TypeError("Observation must be an instance of RepairObservation")
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


RoutingCodingModel = FallbackCodingModel
