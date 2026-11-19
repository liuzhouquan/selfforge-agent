"""Secondary security and hardening tests for review engine and services (SF-REV-02)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from selfforge_agent.models import (
    ChangedFile,
    ChangeKind,
    DiffHunk,
    DiffLine,
    Finding,
    PRContext,
    VerificationStatus,
)
from selfforge_agent.providers import ProviderConfig
from selfforge_agent.review.baseline import BaselineReviewer
from selfforge_agent.review.deepseek import (
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


class TestBaselineReviewerHardening:
    def test_baseline_reviewer_rejects_path_traversal_and_absolute_paths(self) -> None:
        reviewer = BaselineReviewer()
        bad_paths = [
            "../../etc/passwd",
            "../secret.py",
            "/root/secret.py",
            "C:\\Windows\\system32",
            "bad\x00file.py",
            "normal/../../../outside.py",
        ]
        files = [
            ChangedFile(
                path=p,
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,1 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[
                            DiffLine(kind=ChangeKind.added, content="eval('bad')", new_line=1),
                        ],
                    )
                ],
            )
            for p in bad_paths
        ]
        findings = reviewer.review(_make_context(files))
        # Malicious paths must be skipped or rejected; no finding can reference an unsafe path
        for f in findings:
            assert not f.file_path.startswith(("/", "\\"))
            assert ".." not in f.file_path
            assert "\x00" not in f.file_path

    def test_baseline_reviewer_rejects_boolean_new_line(self) -> None:
        reviewer = BaselineReviewer()
        # In Python isinstance(True, int) is True, but new_line must not be a boolean
        line = DiffLine.model_construct(kind=ChangeKind.added, content="eval('bool')", new_line=True)
        hunk = DiffHunk.model_construct(header="@@ -1,1 +1,1 @@", old_start=1, new_start=1, lines=[line])
        changed = ChangedFile.model_construct(path="test.py", status="M", hunks=[hunk])

        findings = reviewer.review(_make_context([changed]))
        # Must not produce a finding with line_number as True or accept bool line numbers
        assert findings == []

    def test_baseline_reviewer_bounds_hunk_lines(self) -> None:
        reviewer = BaselineReviewer()
        # Single hunk with 2000 lines
        lines = [
            DiffLine(kind=ChangeKind.added, content=f"eval({i})", new_line=i + 1)
            for i in range(2000)
        ]
        hunk = DiffHunk(header="@@ -1,2000 +1,2000 @@", old_start=1, new_start=1, lines=lines)
        changed = ChangedFile(path="large_hunk.py", status="M", hunks=[hunk])
        findings = reviewer.review(_make_context([changed]))
        # Should be bounded to MAX_HUNK_LINES (1000)
        assert len(findings) <= 1000

    def test_baseline_reviewer_cleans_null_bytes_in_content(self) -> None:
        reviewer = BaselineReviewer()
        hunk = DiffHunk(
            header="@@ -1,1 +1,1 @@",
            old_start=1,
            new_start=1,
            lines=[
                DiffLine(kind=ChangeKind.added, content="eval(\x00'cleaned'\x00)", new_line=1),
            ],
        )
        changed = ChangedFile(path="clean.py", status="M", hunks=[hunk])
        findings = reviewer.review(_make_context([changed]))
        assert len(findings) == 1
        assert "\x00" not in findings[0].evidence

    def test_baseline_reviewer_findings_sorted_deterministically(self) -> None:
        reviewer = BaselineReviewer()
        # Files and line numbers added out of order
        files = [
            ChangedFile(
                path="b_file.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,2 +1,2 @@",
                        old_start=1,
                        new_start=1,
                        lines=[
                            DiffLine(kind=ChangeKind.added, content="eval('b2')", new_line=20),
                            DiffLine(kind=ChangeKind.added, content="eval('b1')", new_line=5),
                        ],
                    )
                ],
            ),
            ChangedFile(
                path="a_file.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,1 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[
                            DiffLine(kind=ChangeKind.added, content="eval('a1')", new_line=10),
                        ],
                    )
                ],
            ),
        ]
        findings = reviewer.review(_make_context(files))
        assert len(findings) == 3
        # Should be sorted by (file_path, line_number)
        assert findings[0].file_path == "a_file.py"
        assert findings[0].line_number == 10
        assert findings[1].file_path == "b_file.py"
        assert findings[1].line_number == 5
        assert findings[2].file_path == "b_file.py"
        assert findings[2].line_number == 20


class TestProviderReviewerConfigAndInputHardening:
    def test_provider_reviewer_rejects_malicious_model_names(self) -> None:
        bad_models = [
            "",
            "   ",
            "model\x00with_null",
            "model\nwith_newline",
            "model\rwith_cr",
            "a" * 200,  # exceeds max 128 chars
        ]
        for bad_m in bad_models:
            cfg = ProviderConfig(
                name="prov",
                label="Prov",
                key="sk-test",
                model=bad_m,
                base_url="https://api.openai.com/v1",
                status_url="https://api.openai.com/v1/models",
            )
            with pytest.raises(DeepSeekReviewError, match="(?i)model"):
                ProviderReviewer(cfg)

    def test_provider_reviewer_rejects_malicious_base_url_and_key(self) -> None:
        bad_urls = [
            "https://api.openai.com/v1\x00bad",
            "https://api.openai.com/v1 with space",
            "https://" + "a" * 1050 + ".com",
        ]
        for bad_u in bad_urls:
            cfg = ProviderConfig(
                name="prov",
                label="Prov",
                key="sk-test",
                model="gpt-4o",
                base_url=bad_u,
                status_url="https://api.openai.com/v1/models",
            )
            with pytest.raises(DeepSeekReviewError, match="(?i)base_url"):
                ProviderReviewer(cfg)

        bad_keys = [
            "sk-test\x00bad",
            "sk-test\nnewline",
            "sk-test\rcr",
            "k" * 5000,
        ]
        for bad_k in bad_keys:
            cfg = ProviderConfig(
                name="prov",
                label="Prov",
                key=bad_k,
                model="gpt-4o",
                base_url="https://api.openai.com/v1",
                status_url="https://api.openai.com/v1/models",
            )
            with pytest.raises(DeepSeekReviewError, match="(?i)key"):
                ProviderReviewer(cfg)

    def test_provider_reviewer_validates_max_tokens_and_timeout(self) -> None:
        valid_cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        # Timeout exceeding 300 or non-positive
        for bad_to in (301, 1000, 0, -1, float("nan"), True):
            with pytest.raises(DeepSeekReviewError, match="(?i)timeout"):
                ProviderReviewer(valid_cfg, timeout=bad_to)  # type: ignore[arg-type]

        # max_tokens invalid values
        for bad_mt in (0, -1, 9000, True, False, "1200", 12.5):
            with pytest.raises(DeepSeekReviewError, match="(?i)max_tokens"):
                ProviderReviewer(valid_cfg, max_tokens=bad_mt)  # type: ignore[arg-type]

        # Valid max_tokens
        rev = ProviderReviewer(valid_cfg, max_tokens=2048)
        assert rev.max_tokens == 2048

    def test_provider_reviewer_bounds_hunks_and_skips_traversal_paths(self, monkeypatch) -> None:
        cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        reviewer = ProviderReviewer(cfg)

        # 1. Traversal path
        bad_file = ChangedFile(
            path="../../outside.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,1 +1,1 @@",
                    old_start=1,
                    new_start=1,
                    lines=[
                        DiffLine(kind=ChangeKind.added, content="secret_code()", new_line=1),
                    ],
                )
            ],
        )
        # 2. Boolean line number
        bool_line = DiffLine.model_construct(kind=ChangeKind.added, content="bad_line()", new_line=True)
        bool_hunk = DiffHunk.model_construct(header="@@ -1,1 +1,1 @@", old_start=1, new_start=1, lines=[bool_line])
        bool_line_file = ChangedFile.model_construct(path="valid.py", status="M", hunks=[bool_hunk])

        sent_payloads = []

        def mock_request(*args, **kwargs):
            sent_payloads.append(kwargs.get("payload", {}))
            return {"choices": [{"message": {"content": '{"findings": []}'}}]}

        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", mock_request)

        # Traversal and bool lines only -> should find no valid lines to send
        findings, usage = reviewer.review_with_usage(_make_context([bad_file, bool_line_file]))
        assert findings == []
        assert usage is None
        assert len(sent_payloads) == 0  # didn't even need to call model


class TestProviderReviewerOutputAndDefenseHardening:
    def test_provider_reviewer_extracts_json_from_markdown_code_fences(self, monkeypatch) -> None:
        cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        reviewer = ProviderReviewer(cfg)

        ctx = _make_context([
            ChangedFile(
                path="app.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,1 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", new_line=1)],
                    )
                ],
            )
        ])

        # Test case 1: ```json ... ``` markdown code fence
        mock_response_fence = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "```json\n"
                            '{"findings": [{"file_path": "app.py", "line_number": 1, "severity": "high", "category": "security", "description": "dangerous eval"}]}\n'
                            "```"
                        )
                    }
                }
            ]
        }
        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", lambda *a, **kw: mock_response_fence)
        findings, _ = reviewer.review_with_usage(ctx)
        assert len(findings) == 1
        assert findings[0].file_path == "app.py"
        assert findings[0].line_number == 1
        assert findings[0].description == "dangerous eval"

        # Test case 2: text surrounding ``` ... ```
        mock_response_surround = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Here is the code review for your pull request:\n\n"
                            "```\n"
                            '{"findings": [{"file_path": "app.py", "line_number": 1, "severity": "high", "category": "security", "description": "dangerous eval 2"}]}\n'
                            "```\n\nHope this helps!"
                        )
                    }
                }
            ]
        }
        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", lambda *a, **kw: mock_response_surround)
        findings2, _ = reviewer.review_with_usage(ctx)
        assert len(findings2) == 1
        assert findings2[0].description == "dangerous eval 2"

    def test_provider_reviewer_rejects_oversized_response_content(self, monkeypatch) -> None:
        cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        reviewer = ProviderReviewer(cfg)
        ctx = _make_context([
            ChangedFile(
                path="app.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,1 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", new_line=1)],
                    )
                ],
            )
        ])

        # Content > 131_072 chars
        huge_content = '{"findings": []}' + " " * 150_000
        mock_response = {
            "choices": [{"message": {"content": huge_content}}]
        }
        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", lambda *a, **kw: mock_response)
        with pytest.raises(DeepSeekReviewError, match="(?i)size limit"):
            reviewer.review_with_usage(ctx)

    def test_provider_reviewer_sanitizes_severity_category_description(self, monkeypatch) -> None:
        cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        reviewer = ProviderReviewer(cfg)
        ctx = _make_context([
            ChangedFile(
                path="app.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,2 +1,2 @@",
                        old_start=1,
                        new_start=1,
                        lines=[
                            DiffLine(kind=ChangeKind.added, content="eval(1)", new_line=1),
                            DiffLine(kind=ChangeKind.added, content="exec(2)", new_line=2),
                        ],
                    )
                ],
            )
        ])

        # Model returns uppercase severity, unknown severity, null bytes, ANSI escapes, HTML script tags
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"findings": ['
                            '{"file_path": "app.py", "line_number": 1, "severity": " CRITICAL ", "category": "security\\x00vuln", "description": "dangerous code \\x1b[31malert\\x1b[0m <script>alert(1)</script>"},'
                            '{"file_path": "app.py", "line_number": 2, "severity": "unknown_sev", "category": "bug", "description": "defect\\x00here"}'
                            "]}"
                        )
                    }
                }
            ]
        }
        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", lambda *a, **kw: mock_response)
        findings, _ = reviewer.review_with_usage(ctx)
        assert len(findings) == 2
        # CRITICAL -> critical
        assert findings[0].severity == "critical"
        assert "\x00" not in findings[0].category
        assert "\x1b" not in findings[0].description
        assert "<script>" not in findings[0].description
        assert "\x00" not in findings[0].description

        # unknown_sev -> fallback to "medium"
        assert findings[1].severity == "medium"
        assert "\x00" not in findings[1].description

    def test_provider_reviewer_maps_socket_timeout_and_network_errors(self, monkeypatch) -> None:
        cfg = ProviderConfig(
            name="prov",
            label="Prov",
            key="sk-test",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            status_url="https://api.openai.com/v1/models",
        )
        reviewer = ProviderReviewer(cfg)
        ctx = _make_context([
            ChangedFile(
                path="app.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,1 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", new_line=1)],
                    )
                ],
            )
        ])

        def mock_timeout(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", mock_timeout)
        with pytest.raises(DeepSeekReviewError, match="timed out"):
            reviewer.review_with_usage(ctx)

        def mock_oserror(*args, **kwargs):
            raise OSError("Network is unreachable")

        monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", mock_oserror)
        with pytest.raises(DeepSeekReviewError, match="unreachable"):
            reviewer.review_with_usage(ctx)


class TestReviewServiceHardening:
    def test_review_service_rejects_invalid_reviewer_and_runner(self) -> None:
        # Invalid reviewer (missing callable .review)
        with pytest.raises(TypeError, match="reviewer"):
            ReviewService(reviewer="not_a_reviewer")  # type: ignore[arg-type]

        # Invalid runner (missing callable .run)
        with pytest.raises(TypeError, match="runner"):
            ReviewService(runner="not_a_runner")  # type: ignore[arg-type]

    def test_review_service_review_context_validates_and_filters_findings(self) -> None:
        mock_reviewer = MagicMock()
        # Returns a list containing non-Finding items
        mock_reviewer.review.return_value = [
            None,
            "not a finding",
            Finding(
                file_path="app.py",
                line_number=1,
                severity="high",
                category="bug",
                description="real defect",
                evidence="code",
                verification_status=VerificationStatus.suspected,
            ),
        ]
        service = ReviewService(reviewer=mock_reviewer)
        report = service.review_context(_make_context([]))
        # Non-findings must be filtered out or recorded as error
        assert len(report.findings) == 1
        assert report.findings[0].file_path == "app.py"

    def test_review_service_review_context_catches_all_exceptions(self) -> None:
        class ArbitraryCustomError(Exception):
            pass

        mock_reviewer = MagicMock()
        mock_reviewer.review.side_effect = ArbitraryCustomError("completely arbitrary failure")
        service = ReviewService(reviewer=mock_reviewer)
        report = service.review_context(_make_context([]))
        assert report.execution_status == "error"
        assert any("completely arbitrary failure" in err for err in report.errors)

    def test_review_service_review_validates_repository_and_revisions(self, tmp_path) -> None:
        service = ReviewService()

        # Nonexistent repository
        with pytest.raises(ValueError, match="(?i)repository"):
            service.review(tmp_path / "nonexistent", "0" * 40, "1" * 40)

        # File instead of directory
        dummy_file = tmp_path / "file.txt"
        dummy_file.write_text("hello")
        with pytest.raises(ValueError, match="(?i)repository"):
            service.review(dummy_file, "0" * 40, "1" * 40)

        # Bad revisions: option injection, whitespace, null byte
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        for bad_rev in ("--upload-pack=evil", "rev with space", "rev\x00null", ""):
            with pytest.raises(ValueError, match="revision"):
                service.review(repo_dir, bad_rev, "1" * 40)
            with pytest.raises(ValueError, match="revision"):
                service.review(repo_dir, "0" * 40, bad_rev)

        # trusted_tests must be strict bool
        with pytest.raises(TypeError, match="trusted_tests"):
            service.review(repo_dir, "0" * 40, "1" * 40, trusted_tests="yes")  # type: ignore[arg-type]

        # timeout upper bound (> 3600)
        with pytest.raises(ValueError, match="timeout"):
            service.review(repo_dir, "0" * 40, "1" * 40, timeout=7200)
