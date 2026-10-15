"""Bounded repair loop with objective base/head/repaired pytest evidence."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from verified_pr_agent.coding.model_client import CodingModel, ModelError
from verified_pr_agent.coding.models import (
    CodeEdit,
    RepairAttempt,
    RepairObservation,
    RepairReport,
    SourceFile,
)
from verified_pr_agent.coding.worktree import WorktreeError, detached_worktree, git
from verified_pr_agent.core.git_context import build_context
from verified_pr_agent.models import PRContext, TestResult
from verified_pr_agent.review.baseline import BaselineReviewer
from verified_pr_agent.verification.runner import TestRunner, TrustRequiredError

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


def _apply_edit(worktree: Path, edit: CodeEdit, allowed: set[str]) -> None:
    if edit.path not in allowed:
        raise EditRejected("Edit path is not an allowed changed source file")
    path = worktree / edit.path
    if path.is_symlink() or not path.resolve().is_relative_to(worktree.resolve()):
        raise EditRejected("Edit path escapes the temporary worktree")
    if len(edit.new_text.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise EditRejected("Replacement text exceeds the source size limit")
    original = path.read_text(encoding="utf-8")
    if original.count(edit.old_text) != 1 or edit.old_text == edit.new_text:
        raise EditRejected("Old text must occur exactly once and the edit must change it")
    path.write_text(original.replace(edit.old_text, edit.new_text, 1), encoding="utf-8")


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
    def __init__(self, model: CodingModel, runner: TestRunner | None = None) -> None:
        self.model = model
        self.runner = runner or TestRunner()

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
        if not trusted:
            raise TrustRequiredError("Repair requires --trust-local-tests")
        if not task.strip() or not 1 <= max_attempts <= 3:
            raise ValueError("A task and 1–3 repair attempts are required")
        context = build_context(repository, base, head)
        findings = BaselineReviewer().review(context)
        report = RepairReport(
            run_id=str(uuid4()),
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
        try:
            with tempfile.TemporaryDirectory(prefix="verified-pr-repair-") as temp:
                root = Path(temp)
                with (
                    detached_worktree(repo, context.base_sha, root / "base") as base_tree,
                    detached_worktree(repo, context.head_sha, root / "head") as head_tree,
                    detached_worktree(repo, context.head_sha, root / "repair") as repair_tree,
                ):
                    report.base_test = self.runner.run(
                        base_tree, trusted=True, timeout=timeout, test_path=test_path
                    )
                    report.head_test = self.runner.run(
                        head_tree, trusted=True, timeout=timeout, test_path=test_path
                    )
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
                        try:
                            edit = self.model.propose(observation)
                            attempt.edit = edit
                            _apply_edit(repair_tree, edit, allowed)
                            attempt.patch = _safe_patch(repair_tree, allowed)
                            attempt.test_result = self.runner.run(
                                repair_tree, trusted=True, timeout=timeout, test_path=test_path
                            )
                            attempt.patch = _safe_patch(repair_tree, allowed)
                        except (EditRejected, ModelError, OSError, ValueError) as exc:
                            attempt.error = str(exc)
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
        except (OSError, ValueError, WorktreeError) as exc:
            report.status = "error"
            report.errors.append(str(exc))
            return report
        finally:
            report.finished_at = datetime.now(UTC)
