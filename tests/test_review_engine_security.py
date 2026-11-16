"""Security and input defense tests for review engine and services (SF-REV-01)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from selfforge_agent.models import (
    ChangedFile,
    ChangeKind,
    DiffHunk,
    DiffLine,
    PRContext,
)
from selfforge_agent.providers import ProviderConfig
from selfforge_agent.review.baseline import BaselineReviewer
from selfforge_agent.review.deepseek import (
    DeepSeekReviewer,
    DeepSeekReviewError,
    ProviderReviewer,
)
from selfforge_agent.service import ReviewService


def _make_context(files: list[ChangedFile]) -> PRContext:
    return PRContext(
        repository="/tmp/dummy-repo",
        base_sha="0" * 40,
        head_sha="1" * 40,
        files=files,
    )


def test_baseline_reviewer_rejects_invalid_context() -> None:
    reviewer = BaselineReviewer()
    with pytest.raises((ValueError, TypeError), match="PRContext"):
        reviewer.review(None)  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError), match="PRContext"):
        reviewer.review("not-a-context")  # type: ignore[arg-type]


def test_baseline_reviewer_bounds_line_length_and_findings() -> None:
    reviewer = BaselineReviewer()

    # 1. Ultra-long line with eval( in first part followed by huge tail
    long_content = "a = 1; " * 100 + "eval('alert(1)')" + "; b = 2" * 10_000
    hunk = DiffHunk(
        header="@@ -1,1 +1,1 @@",
        old_start=1,
        new_start=1,
        lines=[DiffLine(kind=ChangeKind.added, content=long_content, new_line=1)],
    )
    changed = ChangedFile(path="huge.py", status="M", hunks=[hunk])
    findings = reviewer.review(_make_context([changed]))
    assert len(findings) == 1
    assert len(findings[0].evidence) <= 10_000

    # 2. Bounded findings cap (e.g. 500)
    many_lines = [
        DiffLine(kind=ChangeKind.added, content="eval('1')", new_line=i + 1)
        for i in range(1000)
    ]
    many_hunk = DiffHunk(header="@@ -1,1000 +1,1000 @@", old_start=1, new_start=1, lines=many_lines)
    changed_many = ChangedFile(path="flood.py", status="M", hunks=[many_hunk])
    capped_findings = reviewer.review(_make_context([changed_many]))
    assert len(capped_findings) <= 500


def test_baseline_reviewer_handles_blank_evidence_and_deduplicates() -> None:
    reviewer = BaselineReviewer()

    # 1. Matching pattern where strip() is whitespace (e.g., whitespace trailing pattern)
    # Finding requires evidence: min_length=1.
    hunk = DiffHunk(
        header="@@ -1,2 +1,2 @@",
        old_start=1,
        new_start=1,
        lines=[
            DiffLine(kind=ChangeKind.added, content="   ", new_line=1),
            DiffLine(kind=ChangeKind.added, content="eval(1)", new_line=2),
        ],
    )
    changed = ChangedFile(path="test.py", status="M", hunks=[hunk])
    findings = reviewer.review(_make_context([changed]))
    assert all(len(f.evidence) >= 1 for f in findings)

    # 2. Deduplication on identical (file_path, line_number, category)
    dup_hunk = DiffHunk(
        header="@@ -1,1 +1,1 @@",
        old_start=1,
        new_start=1,
        lines=[
            DiffLine(kind=ChangeKind.added, content="eval('1'); eval('2')", new_line=10),
        ],
    )
    changed_dup = ChangedFile(path="dup.py", status="M", hunks=[dup_hunk])
    dup_findings = reviewer.review(_make_context([changed_dup]))
    assert len(dup_findings) == 1


def test_provider_reviewer_validates_timeout_and_config() -> None:
    valid_cfg = ProviderConfig(
        name="test-prov",
        label="Test",
        key="sk-test",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        status_url="https://api.openai.com/v1/models",
    )

    # 1. Invalid timeouts: NaN, Inf, negative, 0, boolean, strings
    for bad_timeout in (float("nan"), float("inf"), -1.0, 0, True, False, "25"):
        with pytest.raises(DeepSeekReviewError, match="(?i)timeout"):
            ProviderReviewer(valid_cfg, timeout=bad_timeout)  # type: ignore[arg-type]

    # 2. Invalid provider config
    for bad_cfg in (
        None,
        ProviderConfig(name="x", label="X", key="", model="m", base_url="https://api.test.com", status_url="https://api.test.com/status"),
        ProviderConfig(name="x", label="X", key="   ", model="m", base_url="https://api.test.com", status_url="https://api.test.com/status"),
        ProviderConfig(name="x", label="X", key="k", model="m", base_url="ftp://api.test.com", status_url="https://api.test.com/status"),
    ):
        with pytest.raises(DeepSeekReviewError):
            ProviderReviewer(bad_cfg)  # type: ignore[arg-type]

    # 3. DeepSeekReviewer official endpoint validation
    with pytest.raises(DeepSeekReviewError, match="official API endpoint"):
        DeepSeekReviewer("key", base_url="https://evil.com")


def test_provider_reviewer_bounds_prompt_and_defends_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_cfg = ProviderConfig(
        name="mock-prov",
        label="Mock",
        key="sk-mock",
        model="mock-model",
        base_url="https://api.mock.com/v1",
        status_url="https://api.mock.com/status",
    )
    reviewer = ProviderReviewer(valid_cfg)

    # 1. Invalid context rejected
    with pytest.raises(DeepSeekReviewError, match="PRContext"):
        reviewer.review("not-a-context")  # type: ignore[arg-type]

    # 2. Mock model response with hallucinated findings and extra findings
    hunk = DiffHunk(
        header="@@ -1,2 +1,2 @@",
        old_start=1,
        new_start=1,
        lines=[
            DiffLine(kind=ChangeKind.added, content="valid_code_line()", new_line=1),
        ],
    )
    changed = ChangedFile(path="valid.py", status="M", hunks=[hunk])
    ctx = _make_context([changed])

    # Model returns:
    # - 1 valid finding
    # - 1 hallucinated finding for nonexistent file
    # - 1 hallucinated finding for nonexistent line number
    fake_payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"findings": ['
                        '{"file_path": "valid.py", "line_number": 1, "severity": "high", "category": "bug", "description": "real defect"},'
                        '{"file_path": "phantom.py", "line_number": 1, "severity": "high", "category": "bug", "description": "phantom file"},'
                        '{"file_path": "valid.py", "line_number": 999, "severity": "high", "category": "bug", "description": "phantom line"}'
                        "]}"
                    )
                },
                "finish_reason": "stop",
            }
        ]
    }

    mock_request = MagicMock(return_value=fake_payload)
    monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", mock_request)

    findings, _usage = reviewer.review_with_usage(ctx)
    assert len(findings) == 1
    assert findings[0].file_path == "valid.py"
    assert findings[0].line_number == 1
    assert findings[0].description == "real defect"


def test_review_service_input_defense_and_error_handling() -> None:
    service = ReviewService()

    # 1. Invalid context in review_context
    with pytest.raises((ValueError, TypeError), match="PRContext"):
        service.review_context(None)  # type: ignore[arg-type]

    # 2. Reviewer raising unexpected exception is recorded in report.errors
    mock_reviewer = MagicMock()
    mock_reviewer.review.side_effect = RuntimeError("Reviewer engine explosion")
    failing_service = ReviewService(reviewer=mock_reviewer)

    ctx = _make_context([])
    report = failing_service.review_context(ctx)
    assert report.execution_status == "error"
    assert any("Reviewer engine explosion" in err for err in report.errors)

    # 3. Path traversal in test_path during review()
    with pytest.raises(ValueError, match="test_path"):
        service.review(
            Path("/tmp"),
            "0" * 40,
            "1" * 40,
            trusted_tests=True,
            test_path="../../etc/passwd",
        )

    # 4. Non-finite or negative timeout
    for bad_to in (float("nan"), -1.0, 0, True):
        with pytest.raises(ValueError, match="timeout"):
            service.review(
                Path("/tmp"),
                "0" * 40,
                "1" * 40,
                timeout=bad_to,  # type: ignore[arg-type]
            )
