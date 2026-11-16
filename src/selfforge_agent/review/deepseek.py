"""Grounded diff review through configured OpenAI-compatible providers."""

from __future__ import annotations

import json
import math

from pydantic import BaseModel, Field, ValidationError

from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.models import ChangeKind, Finding, PRContext, VerificationStatus
from selfforge_agent.providers import (
    ProviderConfig,
    TokenUsage,
    json_object_options,
    token_usage_from_response,
)

MAX_PROMPT_FILES = 100
MAX_PROMPT_LINES = 200
MAX_PROMPT_LINE_CHARS = 1000
MAX_PROMPT_BYTES = 24_000
MAX_MODEL_FINDINGS = 8


class DeepSeekReviewError(ValueError):
    """A model provider could not produce validated review findings."""

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class _ProposedFinding(BaseModel):
    file_path: str = Field(min_length=1, max_length=512)
    line_number: int = Field(ge=1, le=10_000_000)
    severity: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4096)


class _ReviewOutput(BaseModel):
    findings: list[_ProposedFinding] = Field(default_factory=list, max_length=100)


class ProviderReviewer:
    def __init__(self, provider: ProviderConfig, *, timeout: float = 25) -> None:
        if not isinstance(provider, ProviderConfig):
            raise DeepSeekReviewError("Provider configuration must be a ProviderConfig instance")
        if not isinstance(provider.key, str) or not provider.key.strip():
            raise DeepSeekReviewError("A provider API key is required")
        if not isinstance(provider.base_url, str) or not provider.base_url.startswith(("http://", "https://")):
            raise DeepSeekReviewError("Provider base_url must start with http:// or https://")

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise DeepSeekReviewError("Timeout must be a finite positive number")

        self.provider = provider
        self.timeout = float(timeout)

    def review(self, context: PRContext) -> list[Finding]:
        return self.review_with_usage(context)[0]

    def review_with_usage(self, context: PRContext) -> tuple[list[Finding], TokenUsage | None]:
        if not isinstance(context, PRContext):
            raise DeepSeekReviewError("context must be a valid PRContext instance")

        added: dict[tuple[str, int], str] = {}
        for file in context.files[:MAX_PROMPT_FILES]:
            for hunk in file.hunks:
                for line in hunk.lines:
                    if line.kind == ChangeKind.added and line.new_line is not None:
                        added[(file.path, line.new_line)] = line.content[:MAX_PROMPT_LINE_CHARS]
                        if len(added) >= MAX_PROMPT_LINES:
                            break
                if len(added) >= MAX_PROMPT_LINES:
                    break
            if len(added) >= MAX_PROMPT_LINES:
                break

        if not added:
            return [], None

        lines = [
            {"file_path": path, "line_number": number, "code": text}
            for (path, number), text in list(added.items())[:MAX_PROMPT_LINES]
        ]
        user_content = json.dumps(lines, ensure_ascii=False)
        while len(user_content.encode("utf-8")) > MAX_PROMPT_BYTES and lines:
            lines.pop()
            user_content = json.dumps(lines, ensure_ascii=False)

        payload = {
            "model": self.provider.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Review changed code for concrete defects. Return JSON only: "
                        '{"findings":[{"file_path":"x.py","line_number":1,'
                        '"severity":"high","category":"bug","description":"..."}]}. '
                        "Use only supplied file paths and added line numbers. "
                        "Return at most 8 high-confidence findings. An empty array is valid. "
                        "Do not claim a finding is verified."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            **json_object_options(self.provider.base_url),
            "max_tokens": 1200,
        }
        usage = None
        try:
            response = request_json(
                self.provider.base_url.rstrip("/") + "/chat/completions",
                self.provider.key,
                payload=payload,
                timeout=self.timeout,
            )
            if not isinstance(response, dict):
                raise DeepSeekReviewError("Model response must be an object")
            usage = token_usage_from_response(response)
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise DeepSeekReviewError("Model response missing valid choices array", usage=usage)
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                raise DeepSeekReviewError("Model response was truncated", usage=usage)
            message = choice.get("message")
            if not isinstance(message, dict) or "content" not in message:
                raise DeepSeekReviewError("Model response missing message content", usage=usage)
            proposed = _ReviewOutput.model_validate_json(message["content"])
        except ProviderHTTPError as exc:
            raise DeepSeekReviewError(str(exc)) from exc
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DeepSeekReviewError):
                raise
            raise DeepSeekReviewError("Model returned invalid review JSON", usage=usage) from exc

        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for item in proposed.findings[:MAX_MODEL_FINDINGS]:
            key = (item.file_path, item.line_number)
            unique = (*key, item.category)
            if key not in added or unique in seen:
                continue
            seen.add(unique)
            findings.append(
                Finding(
                    file_path=item.file_path,
                    line_number=item.line_number,
                    severity=item.severity,
                    category=item.category,
                    description=item.description,
                    evidence=added[key].strip() or "<blank added line>",
                    verification_status=VerificationStatus.suspected,
                    source=self.provider.name,
                )
            )
        return findings, usage


class DeepSeekReviewer(ProviderReviewer):
    """Compatibility wrapper for the original DeepSeek-only reviewer."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 25,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise DeepSeekReviewError("A DeepSeek API key is required")
        if not isinstance(base_url, str) or base_url.rstrip("/") != "https://api.deepseek.com":
            raise DeepSeekReviewError("DeepSeek review uses the official API endpoint")
        if not isinstance(model, str) or not model.strip():
            raise DeepSeekReviewError("A model name is required")

        super().__init__(
            ProviderConfig(
                name="deepseek",
                label="DeepSeek",
                key=api_key.strip(),
                model=model.strip(),
                base_url=base_url.rstrip("/"),
                status_url="https://api.deepseek.com/user/balance",
            ),
            timeout=timeout,
        )
