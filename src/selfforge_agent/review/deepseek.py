"""Grounded diff review through configured OpenAI-compatible providers."""

from __future__ import annotations

import json
import math
import posixpath
import re
from urllib.error import URLError

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
MAX_PROMPT_HUNKS = 500
MAX_PROMPT_HUNK_LINES = 1000
MAX_PROMPT_LINES = 200
MAX_PROMPT_LINE_CHARS = 1000
MAX_PROMPT_BYTES = 24_000
MAX_MODEL_FINDINGS = 8
MAX_RESPONSE_CHARS = 131_072  # 128 KiB
MAX_HTTP_TIMEOUT = 300.0

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_SCRIPT_TAG_RE = re.compile(r"<\s*/?\s*script[^>]*>", re.IGNORECASE)


def _is_safe_relative_path(path: str) -> bool:
    if not isinstance(path, str) or not path.strip() or len(path) > 512:
        return False
    if any(c in path for c in ("\x00", "\r", "\n", "\t", "\\")):
        return False
    if path.startswith("/"):
        return False
    norm = posixpath.normpath(path.strip())
    return not (norm in (".", "..") or norm.startswith("../") or "/../" in f"/{norm}/")


def _clean_inline_text(text: str) -> str:
    cleaned = text.replace("\x00", "")
    cleaned = _ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = _SCRIPT_TAG_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip()


def _clean_description(text: str) -> str:
    cleaned = text.replace("\x00", "")
    cleaned = _ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = _SCRIPT_TAG_RE.sub("", cleaned)
    return cleaned.strip()


def _extract_json_content(content: str) -> str:
    text = content.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith(("{", "[")):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    # Normalize pseudo \xHH hex escapes to valid JSON \u00HH
    return re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", text)


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
    def __init__(
        self,
        provider: ProviderConfig,
        *,
        timeout: float = 25,
        max_tokens: int = 1200,
    ) -> None:
        if not isinstance(provider, ProviderConfig):
            raise DeepSeekReviewError("Provider configuration must be a ProviderConfig instance")
        if not isinstance(provider.key, str) or not provider.key.strip():
            raise DeepSeekReviewError("A provider API key is required")
        if any(c in provider.key for c in ("\r", "\n", "\x00")) or len(provider.key) > 4096:
            raise DeepSeekReviewError("Provider key contains invalid characters or exceeds 4096 chars")
        if not isinstance(provider.base_url, str) or not provider.base_url.strip():
            raise DeepSeekReviewError("Provider base_url must be a non-empty string")
        if any(c.isspace() or c == "\x00" for c in provider.base_url) or len(provider.base_url) > 1024:
            raise DeepSeekReviewError("Provider base_url contains whitespace, null bytes or exceeds 1024 chars")
        if not provider.base_url.startswith(("http://", "https://")):
            raise DeepSeekReviewError("Provider base_url must start with http:// or https://")
        if not isinstance(provider.model, str) or not provider.model.strip():
            raise DeepSeekReviewError("Provider model must be a non-empty string")
        if any(c in provider.model for c in ("\r", "\n", "\x00")) or len(provider.model) > 128:
            raise DeepSeekReviewError("Provider model contains invalid characters or exceeds 128 chars")

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > MAX_HTTP_TIMEOUT
        ):
            raise DeepSeekReviewError(f"Timeout must be a finite positive number not exceeding {int(MAX_HTTP_TIMEOUT)}")

        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not (1 <= max_tokens <= 8192)
        ):
            raise DeepSeekReviewError("max_tokens must be an integer between 1 and 8192")

        self.provider = provider
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens)

    def review(self, context: PRContext) -> list[Finding]:
        return self.review_with_usage(context)[0]

    def review_with_usage(self, context: PRContext) -> tuple[list[Finding], TokenUsage | None]:
        if not isinstance(context, PRContext):
            raise DeepSeekReviewError("context must be a valid PRContext instance")

        added: dict[tuple[str, int], str] = {}
        files = getattr(context, "files", [])
        if not isinstance(files, list):
            return [], None

        for file in files[:MAX_PROMPT_FILES]:
            path = getattr(file, "path", None)
            if not isinstance(path, str) or not _is_safe_relative_path(path):
                continue
            safe_path = posixpath.normpath(path.strip())

            hunks = getattr(file, "hunks", [])
            if not isinstance(hunks, list):
                continue

            for hunk in hunks[:MAX_PROMPT_HUNKS]:
                lines = getattr(hunk, "lines", [])
                if not isinstance(lines, list):
                    continue

                for line in lines[:MAX_PROMPT_HUNK_LINES]:
                    kind = getattr(line, "kind", None)
                    if kind != ChangeKind.added and kind != "added":
                        continue
                    new_line = getattr(line, "new_line", None)
                    if new_line is None or isinstance(new_line, bool) or not isinstance(new_line, int):
                        continue
                    if not (1 <= new_line <= 10_000_000):
                        continue

                    raw_content = getattr(line, "content", "")
                    if not isinstance(raw_content, str):
                        continue
                    clean_content = raw_content.replace("\x00", "")[:MAX_PROMPT_LINE_CHARS]
                    added[(safe_path, new_line)] = clean_content
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
            "max_tokens": self.max_tokens,
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
            raw_content = message["content"]
            if not isinstance(raw_content, str):
                raise DeepSeekReviewError("Model response content must be a string", usage=usage)
            if len(raw_content) > MAX_RESPONSE_CHARS:
                raise DeepSeekReviewError("Model response content exceeded size limit", usage=usage)

            cleaned_json = _extract_json_content(raw_content).replace("\x00", "")
            proposed = _ReviewOutput.model_validate_json(cleaned_json)
        except ProviderHTTPError as exc:
            raise DeepSeekReviewError(str(exc)) from exc
        except TimeoutError as exc:
            raise DeepSeekReviewError(f"Model provider request timed out: {exc}", usage=usage) from exc
        except (OSError, URLError) as exc:
            raise DeepSeekReviewError(f"Model provider request failed: {exc}", usage=usage) from exc
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DeepSeekReviewError):
                raise
            raise DeepSeekReviewError("Model returned invalid review JSON", usage=usage) from exc

        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for item in proposed.findings[:MAX_MODEL_FINDINGS]:
            key = (item.file_path, item.line_number)
            if key not in added:
                continue

            # Severity normalization and fallback
            sev = (item.severity or "").strip().lower()
            if sev not in ALLOWED_SEVERITIES:
                sev = "medium"

            # Category sanitization
            cat = _clean_inline_text(item.category or "")
            if not cat:
                cat = "defect"

            # Description sanitization
            desc = _clean_description(item.description or "")
            if not desc:
                desc = "Potential defect identified"

            unique = (*key, cat)
            if unique in seen:
                continue
            seen.add(unique)
            findings.append(
                Finding(
                    file_path=item.file_path,
                    line_number=item.line_number,
                    severity=sev,
                    category=cat[:64],
                    description=desc[:4096],
                    evidence=added[key].strip() or "<blank added line>",
                    verification_status=VerificationStatus.suspected,
                    source=self.provider.name,
                )
            )
        findings.sort(key=lambda f: (f.file_path, f.line_number, f.category, f.severity))
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
        max_tokens: int = 1200,
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
            max_tokens=max_tokens,
        )
