"""Optional DeepSeek review of changed lines, grounded in the supplied PR diff."""

import json
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, ValidationError

from verified_pr_agent.models import ChangeKind, Finding, PRContext, VerificationStatus


class DeepSeekReviewError(ValueError):
    """The model review could not produce validated findings."""


class _ProposedFinding(BaseModel):
    file_path: str
    line_number: int = Field(ge=1)
    severity: str
    category: str
    description: str = Field(min_length=1)


class _ReviewOutput(BaseModel):
    findings: list[_ProposedFinding]


class DeepSeekReviewer:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 45,
    ) -> None:
        if not api_key or not model or timeout <= 0:
            raise DeepSeekReviewError("DeepSeek API key, model, and positive timeout are required")
        if base_url.rstrip("/") != "https://api.deepseek.com":
            raise DeepSeekReviewError("DeepSeek review uses the official API endpoint")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def review(self, context: PRContext) -> list[Finding]:
        added: dict[tuple[str, int], str] = {}
        for file in context.files[:100]:
            for hunk in file.hunks:
                for line in hunk.lines:
                    if line.kind == ChangeKind.added and line.new_line is not None:
                        added[(file.path, line.new_line)] = line.content
        if not added:
            return []
        lines = [
            {"file_path": path, "line_number": number, "code": text}
            for (path, number), text in list(added.items())[:200]
        ]
        user_content = json.dumps(lines, ensure_ascii=False)
        while len(user_content) > 24_000 and lines:
            lines.pop()
            user_content = json.dumps(lines, ensure_ascii=False)
        payload = {
            "model": self.model,
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
        request = Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            deadline = monotonic() + self.timeout
            with urlopen(request, timeout=self.timeout) as response:
                chunks: list[bytes] = []
                size = 0
                socket = getattr(getattr(response.fp, "raw", None), "_sock", None)
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise DeepSeekReviewError("DeepSeek response timed out")
                    if socket is not None:
                        socket.settimeout(remaining)
                    chunk = response.read1(min(8192, 131_073 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 131_072:
                        raise DeepSeekReviewError("DeepSeek response exceeded 128 KiB")
                body = b"".join(chunks)
        except HTTPError as exc:
            raise DeepSeekReviewError(f"DeepSeek returned HTTP {exc.code}") from exc
        except (OSError, URLError) as exc:
            raise DeepSeekReviewError(f"DeepSeek request failed: {exc}") from exc
        try:
            response_data = json.loads(body)
            choice = response_data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise DeepSeekReviewError("DeepSeek response was truncated")
            proposed = _ReviewOutput.model_validate_json(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DeepSeekReviewError):
                raise
            raise DeepSeekReviewError("DeepSeek returned invalid review JSON") from exc
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
                    source="deepseek",
                )
            )
        return findings
