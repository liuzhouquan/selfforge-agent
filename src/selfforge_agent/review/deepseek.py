"""Grounded diff review through configured OpenAI-compatible providers."""

import json

from pydantic import BaseModel, Field, ValidationError

from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json
from selfforge_agent.models import ChangeKind, Finding, PRContext, VerificationStatus
from selfforge_agent.providers import ProviderConfig, TokenUsage, token_usage_from_response


class DeepSeekReviewError(ValueError):
    """A model provider could not produce validated review findings."""

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class _ProposedFinding(BaseModel):
    file_path: str
    line_number: int = Field(ge=1)
    severity: str
    category: str
    description: str = Field(min_length=1)


class _ReviewOutput(BaseModel):
    findings: list[_ProposedFinding]


class ProviderReviewer:
    def __init__(self, provider: ProviderConfig, *, timeout: float = 25) -> None:
        if not provider.key or timeout <= 0:
            raise DeepSeekReviewError("A provider API key and positive timeout are required")
        self.provider = provider
        self.timeout = timeout

    def review(self, context: PRContext) -> list[Finding]:
        return self.review_with_usage(context)[0]

    def review_with_usage(self, context: PRContext) -> tuple[list[Finding], TokenUsage | None]:
        added: dict[tuple[str, int], str] = {}
        for file in context.files[:100]:
            for hunk in file.hunks:
                for line in hunk.lines:
                    if line.kind == ChangeKind.added and line.new_line is not None:
                        added[(file.path, line.new_line)] = line.content
        if not added:
            return [], None
        lines = [
            {"file_path": path, "line_number": number, "code": text}
            for (path, number), text in list(added.items())[:200]
        ]
        user_content = json.dumps(lines, ensure_ascii=False)
        while len(user_content) > 24_000 and lines:
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
            "response_format": {"type": "json_object"},
            "max_tokens": 1200,
        }
        usage = None
        try:
            response = request_json(
                self.provider.base_url + "/chat/completions", self.provider.key,
                payload=payload, timeout=self.timeout,
            )
            usage = token_usage_from_response(response)
            choice = response["choices"][0]
            if choice.get("finish_reason") == "length":
                raise DeepSeekReviewError("Model response was truncated", usage=usage)
            proposed = _ReviewOutput.model_validate_json(choice["message"]["content"])
        except ProviderHTTPError as exc:
            raise DeepSeekReviewError(str(exc)) from exc
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DeepSeekReviewError):
                raise
            raise DeepSeekReviewError("Model returned invalid review JSON", usage=usage) from exc
        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for item in proposed.findings[:8]:
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
        self, api_key: str, *, model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com", timeout: float = 25,
    ) -> None:
        if base_url.rstrip("/") != "https://api.deepseek.com":
            raise DeepSeekReviewError("DeepSeek review uses the official API endpoint")
        super().__init__(
            ProviderConfig(
                name="deepseek", label="DeepSeek", key=api_key, model=model,
                base_url=base_url.rstrip("/"),
                status_url="https://api.deepseek.com/user/balance",
            ),
            timeout=timeout,
        )
