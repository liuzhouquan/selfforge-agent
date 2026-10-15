"""One orchestration path shared by CLI and HTTP."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from verified_pr_agent.core.git_context import build_context
from verified_pr_agent.models import EvidenceReport, PRContext
from verified_pr_agent.review.baseline import BaselineReviewer, ReviewAgent
from verified_pr_agent.verification.runner import TestRunner


class ReviewService:
    def __init__(self, reviewer: ReviewAgent | None = None, runner: TestRunner | None = None) -> None:
        self.reviewer = reviewer or BaselineReviewer()
        self.runner = runner or TestRunner()

    def review_context(self, context: PRContext) -> EvidenceReport:
        started = datetime.now(UTC)
        return EvidenceReport(
            run_id=str(uuid4()),
            repository=context.repository,
            base_sha=context.base_sha,
            head_sha=context.head_sha,
            context=context,
            findings=self.reviewer.review(context),
            test_results=[],
            execution_status="completed",
            started_at=started,
            finished_at=datetime.now(UTC),
            errors=[],
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
        context = build_context(repository, base, head)
        report = self.review_context(context)
        if trusted_tests:
            try:
                result = self.runner.run(
                    Path(context.repository), trusted=True, timeout=timeout, test_path=test_path
                )
                report.test_results.append(result)
                if result.error_message:
                    report.errors.append(result.error_message)
            except (OSError, ValueError) as exc:
                report.errors.append(str(exc))
        report.execution_status = "error" if report.errors else "completed"
        report.finished_at = datetime.now(UTC)
        return report
