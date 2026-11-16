"""One orchestration path shared by CLI and HTTP."""

from __future__ import annotations

import math
import posixpath
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from selfforge_agent.core.git_context import build_context
from selfforge_agent.models import EvidenceReport, Finding, PRContext
from selfforge_agent.review.baseline import BaselineReviewer, ReviewAgent
from selfforge_agent.verification.runner import TestRunner


class ReviewService:
    def __init__(self, reviewer: ReviewAgent | None = None, runner: TestRunner | None = None) -> None:
        self.reviewer = reviewer or BaselineReviewer()
        self.runner = runner or TestRunner()

    def review_context(self, context: PRContext) -> EvidenceReport:
        if not isinstance(context, PRContext):
            raise TypeError("context must be a valid PRContext instance")

        started = datetime.now(UTC)
        findings: list[Finding] = []
        errors: list[str] = []

        try:
            findings = self.reviewer.review(context)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, OSError) as exc:
            errors.append(f"Review engine failed: {exc}")

        return EvidenceReport(
            run_id=str(uuid4()),
            repository=context.repository,
            base_sha=context.base_sha,
            head_sha=context.head_sha,
            context=context,
            findings=findings,
            test_results=[],
            execution_status="error" if errors else "completed",
            started_at=started,
            finished_at=datetime.now(UTC),
            errors=errors,
        )

    def review(
        self,
        repository: Path,
        base: str,
        head: str,
        *,
        trusted_tests: bool = False,
        test_path: str = "tests",
        timeout: float = 60,
    ) -> EvidenceReport:
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a finite positive number")

        if not isinstance(test_path, str) or not test_path.strip():
            raise ValueError("test_path must be a non-empty string")
        clean_path = test_path.strip()
        if any(c in clean_path for c in ("\x00", "\r", "\n", "\t")):
            raise ValueError("test_path contains control characters")
        if clean_path.startswith(("/", "\\")):
            raise ValueError("test_path must not be an absolute path")
        norm_path = posixpath.normpath(clean_path)
        if norm_path in (".", "..") or norm_path.startswith("../") or "/../" in f"/{norm_path}/":
            raise ValueError(f"Path traversal detected in test_path: {test_path!r}")

        context = build_context(repository, base, head)
        report = self.review_context(context)
        if trusted_tests:
            try:
                result = self.runner.run(
                    Path(context.repository), trusted=True, timeout=timeout, test_path=clean_path
                )
                report.test_results.append(result)
                if result.error_message:
                    report.errors.append(result.error_message)
            except (OSError, ValueError, RuntimeError) as exc:
                report.errors.append(str(exc))
        report.execution_status = "error" if report.errors else "completed"
        report.finished_at = datetime.now(UTC)
        return report
