"""Bounded repair loop with objective base/head/repaired pytest evidence."""

import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from selfforge_agent.coding.model_client import CodingModel, ModelError
from selfforge_agent.coding.models import (
    CodeEdit,
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
_MAX_TASK_BYTES = 100_000
_MAX_TIMEOUT_SECONDS = 3_600
_MAX_ATTEMPTS = 3


class EditRejected(ValueError):
    """The model's edit violates the narrow source-edit contract."""


def _editable_files(context: PRContext, worktree: Path) -> list[SourceFile]:
    files: list[SourceFile] = []
    seen: set[str] = set()
    try:
        worktree_resolved = worktree.resolve()
    except OSError:
        return []

    for change in context.files:
        if not change.path or change.path in seen:
            continue
        relative = Path(change.path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".py"
            or "tests" in relative.parts
            or relative.name.startswith("test_")
            or relative.name.endswith("_test.py")
            or change.status.startswith("D")
        ):
            continue
        try:
            path = worktree / relative
            resolved = path.resolve()
            if not resolved.is_relative_to(worktree_resolved):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            st = path.stat()
            if st.st_size > _MAX_SOURCE_BYTES or st.st_size < 0:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        seen.add(change.path)
        files.append(SourceFile(path=change.path, content=content))
        if len(files) == _MAX_FILES:
            break
    return files


def _safe_patch(worktree: Path, allowed: set[str]) -> str:
    try:
        changed = git(worktree, "diff", "--name-only", "-z", "HEAD").split("\0")
    except Exception as exc:
        raise EditRejected(f"Git diff failed: {exc}") from exc
    unexpected = {path for path in changed if path and path not in allowed}
    if unexpected:
        raise EditRejected("Tests or unrelated tracked files changed: " + ", ".join(sorted(unexpected)))
    try:
        patch = git(worktree, "diff", "--no-ext-diff", "HEAD")
    except Exception as exc:
        raise EditRejected(f"Git diff failed: {exc}") from exc
    if not patch.strip():
        raise EditRejected("No source change remains after test execution")
    try:
        git(worktree, "diff", "--check")
    except Exception as exc:
        raise EditRejected(f"Patch contains whitespace/format errors: {exc}") from exc
    return patch


def _test_output(result: TestResult | None) -> str:
    if result is None:
        return ""
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    return (stdout + "\n" + stderr)[-8000:]


class RepairAgent:
    def __init__(
        self,
        model: CodingModel,
        runner: TestRunner | DockerTestRunner | None = None,
        session: SessionStore | None = None,
    ) -> None:
        if model is None or not hasattr(model, "propose") or not callable(model.propose):
            raise TypeError("A valid CodingModel with a callable propose(...) method is required")
        if runner is not None and (not hasattr(runner, "run") or not callable(runner.run)):
            raise TypeError("runner must be None or have a callable run(...) method")
        if session is not None and (not hasattr(session, "record_status") or not hasattr(session, "trace")):
            raise TypeError("session must be None or have callable record_status and trace methods")
        self.model = model
        self.runner = runner or TestRunner()
        self.session = session

    def _trace_test(self, stage: str, result: TestResult | None) -> None:
        if self.session and result is not None:
            try:
                self.session.trace(
                    "repair_test",
                    stage=stage,
                    status=getattr(result, "status", "unknown"),
                    passed=getattr(result, "passed", 0),
                    failed=getattr(result, "failed", 0),
                    errors=getattr(result, "errors", 0),
                    exit_code=getattr(result, "exit_code", 1),
                    duration_seconds=getattr(result, "duration_seconds", 0.0),
                    environment=getattr(result, "environment", "unknown"),
                )
            except (OSError, ValueError, TypeError, AttributeError):
                pass

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
        if type(trusted) is not bool:
            raise TypeError("trusted must be a boolean")
        if not trusted and getattr(self.runner, "requires_trust", True):
            raise TrustRequiredError("Host repair requires --trust-local-tests")
        if not isinstance(task, str):
            raise TypeError("A task string is required")
        task_clean = task.strip()
        if not task_clean:
            raise ValueError("A task and 1–3 repair attempts are required")
        if len(task.encode("utf-8", errors="replace")) > _MAX_TASK_BYTES:
            raise ValueError(f"Task exceeds maximum length of {_MAX_TASK_BYTES} bytes")
        if type(max_attempts) is not int or not (1 <= max_attempts <= _MAX_ATTEMPTS):
            raise ValueError("A task and 1–3 repair attempts are required")
        if (
            type(timeout) is bool
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(f"timeout must be a finite positive number <= {_MAX_TIMEOUT_SECONDS}")
        if (
            not isinstance(test_path, str)
            or not test_path.strip()
            or test_path.startswith("/")
            or ".." in Path(test_path).parts
            or "\0" in test_path
        ):
            raise ValueError("test_path must be a valid relative test directory without traversal")
        if (
            not isinstance(base, str)
            or not base.strip()
            or base.startswith("-")
            or any(c in base for c in ("\0", "\r", "\n"))
        ):
            raise ValueError("base must be a valid non-empty revision string without control characters")
        if (
            not isinstance(head, str)
            or not head.strip()
            or head.startswith("-")
            or any(c in head for c in ("\0", "\r", "\n"))
        ):
            raise ValueError("head must be a valid non-empty revision string without control characters")
        if not isinstance(repository, (Path, str)):
            raise TypeError("repository must be a Path or str")
        repo_path = Path(repository)
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository path does not exist or is not a directory: {repository}")

        context = build_context(repo_path, base, head)
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
        model_attempts = getattr(self.model, "attempts", [])
        attempt_offset = len(model_attempts) if isinstance(model_attempts, list) else 0
        usage_history = getattr(self.model, "usage_history", [])
        usage_offset = len(usage_history) if isinstance(usage_history, list) else 0
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
                    try:
                        report.base_test = self.runner.run(
                            base_tree, trusted=trusted, timeout=timeout, test_path=test_path
                        )
                    except Exception as exc:  # noqa: BLE001 - runner execution failure fail-closed
                        report.status = "error"
                        report.errors.append(f"Base test runner failed: {exc}")
                        return report
                    self._trace_test("base", report.base_test)

                    try:
                        report.head_test = self.runner.run(
                            head_tree, trusted=trusted, timeout=timeout, test_path=test_path
                        )
                    except Exception as exc:  # noqa: BLE001 - runner execution failure fail-closed
                        report.status = "error"
                        report.errors.append(f"Head test runner failed: {exc}")
                        return report
                    self._trace_test("head", report.head_test)

                    for stage, result in (("Base", report.base_test), ("Head", report.head_test)):
                        if result is None or getattr(result, "status", None) in {"error", "timeout"}:
                            report.status = "error"
                            err_msg = (
                                getattr(result, "error_message", None)
                                or (getattr(result, "stdout", "") or "")[-500:]
                            ) if result is not None else "Runner returned None"
                            report.errors.append(
                                f"{stage} test execution failed: {err_msg}"
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
                            if edit is None:
                                raise EditRejected("Model returned None instead of a CodeEdit")
                            if (
                                not hasattr(edit, "path")
                                or not hasattr(edit, "old_text")
                                or not hasattr(edit, "new_text")
                            ):
                                raise EditRejected("Model proposal missing required CodeEdit attributes")
                            if (
                                not isinstance(edit.path, str)
                                or not isinstance(edit.old_text, str)
                                or not isinstance(edit.new_text, str)
                            ):
                                raise EditRejected("CodeEdit attributes must be strings")
                            attempt.edit = edit if isinstance(edit, CodeEdit) else CodeEdit(
                                path=edit.path,
                                old_text=edit.old_text,
                                new_text=edit.new_text,
                                rationale=getattr(edit, "rationale", "repair") or "repair",
                            )
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
                            try:
                                attempt.test_result = self.runner.run(
                                    repair_tree, trusted=trusted, timeout=timeout, test_path=test_path
                                )
                            except Exception as run_exc:
                                raise EditRejected(f"Runner execution failed on repaired tree: {run_exc}") from run_exc
                            self._trace_test("repaired", attempt.test_result)
                            if attempt.test_result is None:
                                raise EditRejected("Runner returned None on repaired tree")
                            attempt.patch = _safe_patch(repair_tree, allowed)
                        except (EditRejected, ModelError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
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
            try:
                from selfforge_agent.runtime.pricing import (
                    estimate_monetary_cost,
                    sum_monetary_costs,
                )

                attempts = getattr(self.model, "attempts", None)
                records = []
                if isinstance(attempts, list):
                    for att in attempts[attempt_offset:]:
                        if att is not None:
                            provider = getattr(att, "provider", "custom") or "custom"
                            model_name = getattr(att, "model", "") or ""
                            usage = getattr(att, "usage", None)
                            records.append((provider, model_name, usage))
                elif isinstance(getattr(self.model, "usage_history", None), list):
                    for usage in self.model.usage_history[usage_offset:]:
                        records.append(("custom", getattr(self.model, "model", "") or "", usage))

                tokens = 0
                cost_items = []
                for provider, model_name, usage in records:
                    if usage is not None:
                        t = getattr(usage, "total_tokens", 0)
                        if isinstance(t, int) and t > 0:
                            tokens += t
                        try:
                            c = estimate_monetary_cost(model_name, usage, provider=provider)
                            if c is not None:
                                cost_items.append(c)
                        except (ValueError, TypeError, KeyError, AttributeError):
                            pass
                report.total_tokens = tokens
                report.monetary_cost = sum_monetary_costs(cost_items) if cost_items else None
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                report.total_tokens = max(0, getattr(report, "total_tokens", 0))
                report.monetary_cost = None

            if self.session:
                try:
                    self.session.record_status(report.status)
                except (OSError, ValueError, RuntimeError):
                    pass
