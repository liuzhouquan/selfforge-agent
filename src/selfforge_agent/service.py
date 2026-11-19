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

MAX_REPORT_FINDINGS = 1000


class ReviewService:
    def __init__(self, reviewer: ReviewAgent | None = None, runner: TestRunner | None = None) -> None:
        if reviewer is not None and (not hasattr(reviewer, "review") or not callable(reviewer.review)):
            raise TypeError("reviewer must have a callable 'review' method")
        if runner is not None and (not hasattr(runner, "run") or not callable(runner.run)):
            raise TypeError("runner must have a callable 'run' method")

        self.reviewer = reviewer or BaselineReviewer()
        self.runner = runner or TestRunner()

    def review_context(self, context: PRContext) -> EvidenceReport:
        if not isinstance(context, PRContext):
            raise TypeError("context must be a valid PRContext instance")

        started = datetime.now(UTC)
        findings: list[Finding] = []
        errors: list[str] = []

        try:
            raw_findings = self.reviewer.review(context)
            if isinstance(raw_findings, list):
                for item in raw_findings:
                    if isinstance(item, Finding):
                        findings.append(item)
                    else:
                        errors.append(f"Ignored non-Finding item from review engine: {type(item).__name__}")
                    if len(findings) >= MAX_REPORT_FINDINGS:
                        break
            elif raw_findings is not None:
                errors.append(f"Review engine returned non-list result: {type(raw_findings).__name__}")
        except Exception as exc:  # noqa: BLE001 - isolate all review engine failures fail-closed
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
        repository: Path | str,
        base: str,
        head: str,
        *,
        trusted_tests: bool = False,
        test_path: str = "tests",
        timeout: float = 60,
    ) -> EvidenceReport:
        if not isinstance(repository, (str, Path)):
            raise TypeError("repository must be a Path or str")
        repo_path = Path(repository)
        if not repo_path.is_dir():
            raise ValueError(f"Repository directory does not exist or is not a directory: {repository}")

        for name, rev in (("base", base), ("head", head)):
            if not isinstance(rev, str) or not rev.strip():
                raise ValueError(f"{name} revision must be a non-empty string")
            rev_clean = rev.strip()
            if any(c.isspace() or c == "\x00" for c in rev_clean):
                raise ValueError(f"{name} revision contains whitespace or null bytes: {rev!r}")
            if rev_clean.startswith("-"):
                raise ValueError(f"{name} revision must not start with a hyphen: {rev!r}")
            if len(rev_clean) > 128:
                raise ValueError(f"{name} revision exceeds 128 characters: {rev!r}")

        if not isinstance(trusted_tests, bool):
            raise TypeError("trusted_tests must be a boolean")

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > 3600
        ):
            raise ValueError("timeout must be a finite positive number not exceeding 3600 seconds")

        if not isinstance(test_path, str) or not test_path.strip():
            raise ValueError("test_path must be a non-empty string")
        clean_path = test_path.strip()
        if len(clean_path) > 256:
            raise ValueError("test_path exceeds maximum length of 256 characters")
        if any(c in clean_path for c in ("\x00", "\r", "\n", "\t")):
            raise ValueError("test_path contains control characters")
        if clean_path.startswith(("/", "\\")):
            raise ValueError("test_path must not be an absolute path")
        norm_path = posixpath.normpath(clean_path)
        if norm_path in (".", "..") or norm_path.startswith("../") or "/../" in f"/{norm_path}/":
            raise ValueError(f"Path traversal detected in test_path: {test_path!r}")

        context = build_context(repo_path, base.strip(), head.strip())
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
