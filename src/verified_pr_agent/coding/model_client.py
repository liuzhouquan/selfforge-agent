"""Small OpenAI-compatible chat adapter; model output is validated before any edit."""

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from verified_pr_agent.coding.models import CodeEdit, RepairObservation


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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(131073)
        except HTTPError as exc:
            raise ModelError(f"Model endpoint returned HTTP {exc.code}") from exc
        except (OSError, URLError) as exc:
            raise ModelError(f"Model request failed: {exc}") from exc
        if len(body) > 131072:
            raise ModelError("Model response exceeded 128 KiB")
        try:
            content = json.loads(body)["choices"][0]["message"]["content"]
            return CodeEdit.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelError("Model did not return a valid CodeEdit JSON object") from exc
