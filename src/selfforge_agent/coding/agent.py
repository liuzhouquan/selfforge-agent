"""Bounded repair loop with objective base/head/repaired pytest evidence."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from selfforge_agent.coding.model_client import CodingModel, ModelError
from selfforge_agent.coding.models import (
    RepairAttempt,
    RepairObservation,
    RepairReport,
    SourceFile,
)
from selfforge_agent.coding.worktree import WorktreeError, detached_worktree, git
from selfforge_agent.core.git_context import build_context
from selfforge_agent.models import PRContext, TestResult
from selfforge_agent.review.baseline import BaselineReviewer
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import EditPolicy, ToolRegistry
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner, TrustRequiredError

_MAX_SOURCE_BYTES = 16_384
_MAX_FILES = 8


class EditRejected(ValueError):
    """The model's edit violates the narrow source-edit contract."""


def _editable_files(context: PRContext, worktree: Path) -> list[SourceFile]:
    files: list[SourceFile] = []
    for change in context.files:
        relative = Path(change.path)
        if (
            relative.suffix != ".py"
            or "tests" in relative.parts
            or relative.name.startswith("test_")
            or relative.name.endswith("_test.py")
            or change.status.startswith("D")
        ):
            continue
        path = worktree / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
            continue
        try:
            files.append(SourceFile(path=change.path, content=path.read_text(encoding="utf-8")))
        except UnicodeError:
            continue
        if len(files) == _MAX_FILES:
            break
    return files


def _safe_patch(worktree: Path, allowed: set[str]) -> str:
    changed = git(worktree, "diff", "--name-only", "-z", "HEAD").split("\0")
    unexpected = {path for path in changed if path and path not in allowed}
    if unexpected:
        raise EditRejected("Tests or unrelated tracked files changed: " + ", ".join(sorted(unexpected)))
    patch = git(worktree, "diff", "--no-ext-diff", "HEAD")
    if not patch.strip():
        raise EditRejected("No source change remains after test execution")
    git(worktree, "diff", "--check")
    return patch


def _test_output(result: TestResult) -> str:
    return (result.stdout + "\n" + result.stderr)[-8000:]


class RepairAgent:
    def __init__(self, model: CodingModel, runner: TestRunner | DockerTestRunner | None = None,
                 session: SessionStore | None = None) -> None:
        self.model = model
        self.runner = runner or TestRunner()
        self.session = session

    def _trace_test(self, stage: str, result: TestResult) -> None:
        if self.session:
            self.session.trace("repair_test", stage=stage, status=result.status,
                               passed=result.passed, failed=result.failed,
                               errors=result.errors, exit_code=result.exit_code,
                               duration_seconds=result.duration_seconds,
                               environment=result.environment)

    def run(
        self,
        repository: Path,
        base: str,
        head: str,
        task: str,
        *,
        trusted: bool = False,
        test_path: str = "tests",
        timeout: float = 60,
        max_attempts: int = 2,
    ) -> RepairReport:
        if not trusted and getattr(self.runner, "requires_trust", True):
            raise TrustRequiredError("Host repair requires --trust-local-tests")
        if not task.strip() or not 1 <= max_attempts <= 3:
            raise ValueError("A task and 1–3 repair attempts are required")
        context = build_context(repository, base, head)
        findings = BaselineReviewer().review(context)
        report = RepairReport(
            run_id=self.session.session_id if self.session else str(uuid4()),
            repository=context.repository,
            base_sha=context.base_sha,
            head_sha=context.head_sha,
            task=task,
            context=context,
            findings=findings,
            status="inconclusive",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        repo = Path(context.repository)
        if self.session:
            self.session.record_status("running")
            self.session.trace("repair_start", sandbox="host" if getattr(self.runner, "requires_trust", True) else "docker",
                               base_sha=context.base_sha, head_sha=context.head_sha,
                               max_attempts=max_attempts)
        try:
            with tempfile.TemporaryDirectory(prefix="verified-pr-repair-") as temp:
                root = Path(temp)
                with (
                    detached_worktree(repo, context.base_sha, root / "base") as base_tree,
                    detached_worktree(repo, context.head_sha, root / "head") as head_tree,
                    detached_worktree(repo, context.head_sha, root / "repair") as repair_tree,
                ):
                    report.base_test = self.runner.run(
                        base_tree, trusted=trusted, timeout=timeout, test_path=test_path
                    )
                    self._trace_test("base", report.base_test)
                    report.head_test = self.runner.run(
                        head_tree, trusted=trusted, timeout=timeout, test_path=test_path
                    )
                    self._trace_test("head", report.head_test)
                    for stage, result in (("Base", report.base_test), ("Head", report.head_test)):
                        if result.status in {"error", "timeout"}:
                            report.status = "error"
                            report.errors.append(
                                f"{stage} test execution failed: {result.error_message or result.stdout[-500:]}"
                            )
                            return report
                    if report.base_test.status != "passed":
                        report.errors.append("Base tests did not pass; regression is not established")
                        return report
                    if (
                        report.head_test.status != "failed"
                        or report.head_test.failed == 0
                        or report.head_test.errors != 0
                    ):
                        report.errors.append("Head has no clean assertion failure to repair")
                        return report
                    initial_files = _editable_files(context, repair_tree)
                    allowed = {source.path for source in initial_files}
                    if not allowed:
                        report.errors.append("No small changed Python source file is editable")
                        return report
                    tools = ToolRegistry(
                        repair_tree,
                        edit_policy=EditPolicy(
                            allowed_paths=frozenset(allowed),
                            max_file_bytes=_MAX_SOURCE_BYTES,
                            allow_create=False,
                        ),
                    )
                    failure_output = _test_output(report.head_test)
                    feedback = ""
                    report.status = "unresolved"
                    for number in range(1, max_attempts + 1):
                        observation = RepairObservation(
                            task=task,
                            attempt=number,
                            files=_editable_files(context, repair_tree),
                            findings=findings[:20],
                            failing_test_output=failure_output,
                            feedback=feedback,
                        )
                        attempt = RepairAttempt(number=number)
                        report.attempts.append(attempt)
                        if self.session:
                            self.session.trace("repair_attempt", number=number)
                        try:
                            edit = self.model.propose(observation)
                            attempt.edit = edit
                            if self.session:
                                self.session.trace("repair_edit", number=number, path=edit.path)
                            outcome = tools.execute("edit_file", {
                                "path": edit.path,
                                "old_text": edit.old_text,
                                "new_text": edit.new_text,
                            })
                            if self.session:
                                self.session.trace("tool", workflow="repair", name="edit_file",
                                                   ok=outcome.ok,
                                                   duration_seconds=round(outcome.elapsed_seconds, 3))
                            if not outcome.ok:
                                raise EditRejected(outcome.content.removeprefix("Tool error: "))
                            attempt.patch = _safe_patch(repair_tree, allowed)
                            attempt.test_result = self.runner.run(
                                repair_tree, trusted=trusted, timeout=timeout, test_path=test_path
                            )
                            self._trace_test("repaired", attempt.test_result)
                            attempt.patch = _safe_patch(repair_tree, allowed)
                        except (EditRejected, ModelError, OSError, ValueError) as exc:
                            attempt.error = str(exc)
                            if self.session:
                                self.session.trace("repair_attempt_error", number=number,
                                                   error_type=type(exc).__name__)
                            feedback = attempt.error
                            continue
                        if attempt.test_result.status == "passed":
                            report.status = "verified"
                            report.verified_patch = attempt.patch
                            return report
                        if attempt.test_result.status in {"error", "timeout"}:
                            report.status = "error"
                            report.errors.append(
                                attempt.test_result.error_message or "Repair test execution failed"
                            )
                            return report
                        failure_output = _test_output(attempt.test_result)
                        feedback = f"Previous edit did not pass: {attempt.test_result.status}"
                    return report
        except (OSError, ValueError, RuntimeError, WorktreeError) as exc:
            report.status = "error"
            report.errors.append(str(exc))
            return report
        finally:
            report.finished_at = datetime.now(UTC)
            if self.session:
                self.session.record_status(report.status)
