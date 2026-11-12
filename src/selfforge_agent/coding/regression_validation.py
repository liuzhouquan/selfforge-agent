"""Independent regression validation of a bound Coding workspace against its base commit."""

from __future__ import annotations

import errno
import hashlib
import math
import os
import secrets
import stat
import subprocess
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from selfforge_agent.coding.test_generation import MAX_TEST_CANDIDATE_BYTES, remaining_budget
from selfforge_agent.coding.worktree import WorktreeError, detached_worktree, git
from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import (
    _stage_workspace,
    _StageTimedOut,
    resolve_docker_image,
    workspace_entry_excluded,
)
from selfforge_agent.runtime.fingerprint import WorkspaceSnapshotError, workspace_fingerprint
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import _protected
from selfforge_agent.runtime.workspace import (
    CodingWorkspace,
    CodingWorkspaceManager,
    WorkspaceError,
)
from selfforge_agent.verification.runner import DockerTestRunner

DEFAULT_REGRESSION_TIMEOUT = 60.0
MIN_REGRESSION_TIMEOUT = 1.0
MAX_REGRESSION_TIMEOUT = 180.0
_IMAGE_CHECK_CAP_SECONDS = 10.0

RegressionStatus = Literal["confirmed", "inconclusive", "error"]
RegressionReason = Literal[
    "base_failed_repaired_passed",
    "both_passed",
    "base_did_not_fail",
    "repaired_failed",
    "repaired_had_skipped_tests",
    "workspace_changed",
    "empty_or_all_skipped",
    "collection_or_runtime_error",
    "timeout",
    "snapshot_error",
]


class RegressionValidationError(ValueError):
    """Base fail-closed preflight validation error (maps to HTTP 400 / CLI exit 2)."""


class RegressionNotFoundError(RegressionValidationError):
    """Missing repository, Coding session/workspace, or candidate file (maps to HTTP 404)."""


class RegressionConflictError(RegressionValidationError):
    """Concurrent lock or workspace conflict preventing validation (maps to HTTP 409)."""


class RegressionRunSummary(BaseModel):
    """Sanitized summary of one isolated pytest execution without raw output or file paths."""

    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: int | None = None
    duration_seconds: float
    passed: int
    failed: int
    errors: int
    skipped: int


class ValidateRegressionRequest(BaseModel):
    """API request payload for validating a candidate regression test."""

    repository: str = Field(min_length=1)
    test_file: str = Field(min_length=1)
    timeout: int | float = DEFAULT_REGRESSION_TIMEOUT

    @field_validator("timeout", mode="before")
    @classmethod
    def _validate_timeout_field(cls, value: object) -> float:
        return validate_timeout(value)


class RegressionValidationReport(BaseModel):
    """Sanitized result of validating a single bounded regression test on base vs repaired."""

    status: RegressionStatus
    reason: RegressionReason
    session_id: str
    base_sha: str
    workspace_sha256: str
    test_sha256: str
    base_result: RegressionRunSummary | None = None
    repaired_result: RegressionRunSummary | None = None


def validate_timeout(value: object) -> float:
    """Validate total regression budget strictly within ``1..180`` seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegressionValidationError(
            f"Timeout must be a number between {int(MIN_REGRESSION_TIMEOUT)} and "
            f"{int(MAX_REGRESSION_TIMEOUT)} seconds"
        )
    seconds = float(value)
    if not math.isfinite(seconds) or not (MIN_REGRESSION_TIMEOUT <= seconds <= MAX_REGRESSION_TIMEOUT):
        raise RegressionValidationError(
            f"Timeout must be between {int(MIN_REGRESSION_TIMEOUT)} and "
            f"{int(MAX_REGRESSION_TIMEOUT)} seconds"
        )
    return seconds


def _summarize_result(result: TestResult) -> RegressionRunSummary:
    return RegressionRunSummary(
        status=result.status,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        passed=result.passed,
        failed=result.failed,
        errors=result.errors,
        skipped=result.skipped,
    )


def _is_empty_or_all_skipped(result: TestResult) -> bool:
    total = result.passed + result.failed + result.errors + result.skipped
    return result.status == "error" and (total == 0 or result.skipped == total)


def _classify_outcome(
    base_result: TestResult | None,
    repaired_result: TestResult | None,
    *,
    changed: bool,
    timed_out: bool = False,
) -> tuple[RegressionStatus, RegressionReason]:
    if timed_out or (
        base_result is not None and base_result.status == "timeout"
    ) or (
        repaired_result is not None and repaired_result.status == "timeout"
    ):
        return "error", "timeout"
    if base_result is None or repaired_result is None:
        if changed:
            return "inconclusive", "workspace_changed"
        return "error", "snapshot_error"
    if _is_empty_or_all_skipped(base_result) or _is_empty_or_all_skipped(repaired_result):
        return "error", "empty_or_all_skipped"
    if (
        base_result.status == "error"
        or repaired_result.status == "error"
        or base_result.errors > 0
        or repaired_result.errors > 0
    ):
        return "error", "collection_or_runtime_error"
    if changed:
        return "inconclusive", "workspace_changed"
    if (
        base_result.status == "failed"
        and base_result.exit_code == 1
        and base_result.failed > 0
        and any(
            failure.kind == "failure"
            and (failure.message == "AssertionError"
                 or failure.message.startswith(("assert ", "AssertionError:")))
            for failure in base_result.failure_details
        )
        and base_result.errors == 0
        and repaired_result.status == "passed"
        and repaired_result.exit_code == 0
        and repaired_result.passed > 0
        and repaired_result.failed == 0
        and repaired_result.errors == 0
        and repaired_result.skipped == 0
    ):
        return "confirmed", "base_failed_repaired_passed"
    if repaired_result.skipped > 0 and repaired_result.status == "passed":
        return "inconclusive", "repaired_had_skipped_tests"
    if base_result.status == "passed" and repaired_result.status == "passed":
        return "inconclusive", "both_passed"
    if repaired_result.status == "failed":
        return "inconclusive", "repaired_failed"
    return "inconclusive", "base_did_not_fail"


def _validate_candidate_components(rel_path: Path) -> None:
    if not rel_path.parts:
        raise RegressionValidationError("Candidate test path must name a Python file")
    if rel_path.suffix != ".py":
        raise RegressionValidationError("Candidate test must be a .py file")
    for part in rel_path.parts:
        if part in {"", ".", ".."}:
            raise RegressionValidationError("Candidate test path must not contain traversal segments")
        if _protected(part) or workspace_entry_excluded(part):
            raise RegressionValidationError(f"Protected or excluded path component: {part}")


def _lexists_or_symlink_inside(root: Path, rel_path: Path) -> tuple[bool, bool]:
    """Return ``(exists_anywhere_along_prefix, hit_symlink)`` without following symlinks."""
    current = root
    for part in rel_path.parts:
        current = current / part
        try:
            st = os.lstat(current)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return False, False
            return True, False
        if stat.S_ISLNK(st.st_mode):
            return True, True
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            return True, False
    return True, False


def _read_fd_bounded(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = os.read(fd, (limit + 1) - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    raw = b"".join(chunks)
    if len(raw) > limit:
        raise RegressionValidationError(
            f"Candidate test exceeds maximum size of {limit} bytes"
        )
    return raw


def _open_and_read_relative_candidate(root: Path, rel_path: Path) -> tuple[bytes, str]:
    """Traverse *rel_path* under *root* with ``O_NOFOLLOW`` and bounded regular-file read."""
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)

    try:
        cur_fd = os.open(root, dir_flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RegressionValidationError("Workspace or repository root must not be a symlink") from exc
        if exc.errno == errno.ENOENT:
            raise RegressionNotFoundError("Workspace or repository root not found") from exc
        raise RegressionValidationError("Unable to open workspace or repository root") from exc

    try:
        for part in rel_path.parts[:-1]:
            try:
                next_fd = os.open(part, dir_flags, dir_fd=cur_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise RegressionValidationError(
                        "Candidate test path must not traverse symlinks"
                    ) from exc
                if exc.errno == errno.ENOENT:
                    raise RegressionNotFoundError("Candidate test file not found") from exc
                raise RegressionValidationError("Invalid candidate test directory") from exc
            os.close(cur_fd)
            cur_fd = next_fd

        try:
            file_fd = os.open(rel_path.parts[-1], file_flags, dir_fd=cur_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise RegressionValidationError("Candidate test file must not be a symlink") from exc
            if exc.errno == errno.ENOENT:
                raise RegressionNotFoundError("Candidate test file not found") from exc
            raise RegressionValidationError("Unable to open candidate test file") from exc
    finally:
        os.close(cur_fd)

    try:
        st = os.fstat(file_fd)
        if not stat.S_ISREG(st.st_mode):
            raise RegressionValidationError("Candidate test must be a regular file")
        if st.st_size > MAX_TEST_CANDIDATE_BYTES:
            raise RegressionValidationError(
                f"Candidate test exceeds maximum size of {MAX_TEST_CANDIDATE_BYTES} bytes"
            )
        raw = _read_fd_bounded(file_fd, MAX_TEST_CANDIDATE_BYTES)
    finally:
        os.close(file_fd)

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegressionValidationError("Candidate test must be valid UTF-8") from exc
    return raw, hashlib.sha256(raw).hexdigest()


def _resolve_and_read_candidate(
    raw_test_file: str | Path,
    *,
    raw_repository: Path,
    workspace: CodingWorkspace,
) -> tuple[Path, Path, bytes, str]:
    raw_str = os.fspath(raw_test_file)
    if not raw_str or not raw_str.strip() or "\x00" in raw_str:
        raise RegressionValidationError("Candidate test path is invalid")

    candidate = Path(raw_str)
    if ".." in candidate.parts:
        raise RegressionValidationError("Candidate test path must not contain '..'")

    if raw_repository.is_symlink() or workspace.path.is_symlink() or workspace.repository.is_symlink():
        raise RegressionValidationError("Repository and workspace paths must not be symlinks")

    workspace_root = workspace.path.resolve()
    repo_root = workspace.repository.resolve()

    if candidate.is_absolute():
        target_root: Path | None = None
        rel_path: Path | None = None
        for root in (workspace_root, repo_root):
            if candidate.is_relative_to(root):
                target_root = root
                rel_path = candidate.relative_to(root)
                break
        if target_root is None or rel_path is None:
            raise RegressionValidationError(
                "Candidate test must reside inside the workspace or bound repository"
            )
        _validate_candidate_components(rel_path)
        raw_bytes, digest = _open_and_read_relative_candidate(target_root, rel_path)
        return target_root, rel_path, raw_bytes, digest

    _validate_candidate_components(candidate)
    roots = (workspace_root,) if workspace_root == repo_root else (workspace_root, repo_root)
    for root in roots:
        exists, hit_symlink = _lexists_or_symlink_inside(root, candidate)
        if hit_symlink:
            raise RegressionValidationError("Candidate test path must not reference symlinks")
        if exists:
            raw_bytes, digest = _open_and_read_relative_candidate(root, candidate)
            return root, candidate, raw_bytes, digest

    raise RegressionNotFoundError("Candidate test file not found")


def _load_bound_docker_image(
    workspace_manager: CodingWorkspaceManager,
    workspace: CodingWorkspace,
    *,
    legacy_session_roots: Sequence[Path],
    deadline: float,
) -> tuple[SessionStore, Path, str]:
    session_root = workspace_manager.session_root(
        workspace.repository,
        workspace.session_id,
        legacy_roots=legacy_session_roots,
    )
    store = SessionStore(session_root, workspace.session_id)
    record_path = store.path.with_name(f"{store.session_id}.regression.jsonl")
    if store.path.is_symlink() or store.trace_path.is_symlink() or record_path.is_symlink():
        raise RegressionValidationError("Session storage path must not be a symlink")
    if not store.path.exists():
        raise RegressionNotFoundError(f"Unknown session: {workspace.session_id}")
    if not store.path.is_file():
        raise RegressionValidationError("Invalid session record file")

    try:
        store.load()
        env = store.environment()
    except OSError as exc:
        raise RegressionValidationError("Session record could not be read") from exc
    except ValueError as exc:
        raise RegressionValidationError("Corrupt or unreadable session") from exc

    if env is None:
        raise RegressionValidationError("Session has no bound execution environment")
    if env.get("sandbox") != "docker":
        raise RegressionValidationError(
            "Regression validation supports only docker-bound Coding sessions"
        )
    image_id = env.get("image_id")
    if not isinstance(image_id, str) or not image_id.strip():
        raise RegressionValidationError("Session is missing a bound Docker image")

    budget = min(
        _IMAGE_CHECK_CAP_SECONDS,
        remaining_budget(deadline, message="Regression validation budget exhausted"),
    )
    try:
        resolved_image = resolve_docker_image(image_id, timeout=budget)
    except ValueError as exc:
        raise RegressionValidationError("Bound Docker image is unavailable") from exc
    return store, record_path, resolved_image


def _validate_base_commit(workspace: CodingWorkspace, *, deadline: float) -> None:
    try:
        git(workspace.repository, "cat-file", "-e", f"{workspace.base_sha}^{{commit}}", deadline=deadline)
    except WorktreeError as exc:
        raise RegressionValidationError(
            f"Workspace base_sha {workspace.base_sha!r} is not a valid commit in the bound repository"
        ) from exc


def _resolve_workspace(
    workspace_manager: CodingWorkspaceManager,
    raw_repo: Path,
    session_id: str,
    *, deadline: float,
) -> CodingWorkspace:
    if (
        not isinstance(session_id, str)
        or not session_id
        or "\x00" in session_id
        or "/" in session_id
        or "\\" in session_id
        or ".." in session_id
    ):
        raise RegressionValidationError("Invalid session_id")

    if raw_repo.is_symlink():
        raise RegressionValidationError("Repository path must not be a symlink")
    if not raw_repo.exists() or not raw_repo.is_dir():
        raise RegressionNotFoundError("Repository directory not found")

    try:
        workspace = workspace_manager.get(raw_repo, session_id, deadline=deadline)
    except WorkspaceError as exc:
        msg = str(exc)
        if "unknown coding workspace" in msg.lower():
            raise RegressionNotFoundError(msg) from exc
        raise RegressionValidationError("Invalid or mismatched Coding workspace") from exc

    if workspace.path.is_symlink() or workspace.repository.is_symlink():
        raise RegressionValidationError("Workspace or repository path must not be a symlink")
    return workspace


def _stage_candidate_filename(directory: Path) -> str:
    for _ in range(16):
        name = f"test_selfforge_regression_{secrets.token_hex(6)}.py"
        candidate = directory / name
        if not candidate.exists() and not candidate.is_symlink():
            return name
    raise RegressionValidationError("Unable to allocate unique staged test filename")


def _stage_base_snapshot(
    workspace: CodingWorkspace,
    snapshot_dir: Path,
    *,
    scratch_root: Path,
    deadline: float,
) -> None:
    checkout_dir = scratch_root / "base_checkout"
    with detached_worktree(workspace.repository, workspace.base_sha, checkout_dir, deadline=deadline) as base_wt:
        _stage_workspace(base_wt, snapshot_dir, deadline - 0.25, None)


def _run_snapshot_test(
    runner: DockerTestRunner,
    snapshot_dir: Path,
    candidate_bytes: bytes,
    *,
    deadline: float,
) -> TestResult:
    staged_name = _stage_candidate_filename(snapshot_dir)
    staged_path = snapshot_dir / staged_name
    fd = os.open(
        staged_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(candidate_bytes)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
    finally:
        os.close(fd)

    budget = remaining_budget(deadline, message="Regression validation budget exhausted")
    return runner.run(snapshot_dir, trusted=True, timeout=budget, test_path=staged_name)


def _check_unchanged(
    workspace_path: Path,
    before_fp: str,
    candidate_root: Path,
    candidate_rel: Path,
    expected_test_sha: str,
    *,
    deadline: float,
) -> bool:
    current_fp = workspace_fingerprint(workspace_path, deadline=deadline)
    if current_fp != before_fp:
        return True
    try:
        _, current_test_sha = _open_and_read_relative_candidate(candidate_root, candidate_rel)
    except RegressionValidationError:
        return True
    return current_test_sha != expected_test_sha


def _record_validation_metadata(
    store: SessionStore,
    record_path: Path,
    report: RegressionValidationReport,
) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": report.status,
        "reason": report.reason,
        "session_id": report.session_id,
        "base_sha": report.base_sha,
        "workspace_sha256": report.workspace_sha256,
        "test_sha256": report.test_sha256,
        "base_status": report.base_result.status if report.base_result else None,
        "repaired_status": report.repaired_result.status if report.repaired_result else None,
    }
    SessionStore._append(record_path, payload)
    store.trace(
        "regression_validation",
        status=report.status,
        reason=report.reason,
        base_sha=report.base_sha,
        workspace_sha256=report.workspace_sha256,
        test_sha256=report.test_sha256,
    )


def validate_workspace_regression(
    repository: str | Path,
    session_id: str,
    test_file: str | Path,
    *,
    timeout: float = DEFAULT_REGRESSION_TIMEOUT,
    workspace_manager: CodingWorkspaceManager | None = None,
    legacy_session_roots: Sequence[Path] = (),
) -> RegressionValidationReport:
    """Validate an independent bounded regression test on a Docker-bound Coding workspace."""
    budget_seconds = validate_timeout(timeout)
    deadline = time.monotonic() + budget_seconds

    manager = workspace_manager or CodingWorkspaceManager()
    raw_repo = Path(repository)
    workspace = _resolve_workspace(manager, raw_repo, session_id, deadline=deadline)

    try:
        with manager.guard(
            workspace.path,
            message=f"Coding workspace is busy running another job for session {workspace.session_id}",
        ):
            _validate_base_commit(workspace, deadline=deadline)
            store, record_path, bound_image = _load_bound_docker_image(
                manager,
                workspace,
                legacy_session_roots=legacy_session_roots,
                deadline=deadline,
            )
            candidate_root, candidate_rel, candidate_bytes, test_sha256 = (
                _resolve_and_read_candidate(
                    test_file,
                    raw_repository=raw_repo,
                    workspace=workspace,
                )
            )
            return _execute_isolated_validation(
                workspace=workspace,
                store=store,
                record_path=record_path,
                bound_image=bound_image,
                candidate_root=candidate_root,
                candidate_rel=candidate_rel,
                candidate_bytes=candidate_bytes,
                test_sha256=test_sha256,
                deadline=deadline,
            )
    except WorkspaceError as exc:
        msg = str(exc)
        if "busy" in msg.lower():
            raise RegressionConflictError(msg) from exc
        raise RegressionValidationError("Invalid or mismatched Coding workspace") from exc


def _execute_isolated_validation(
    *,
    workspace: CodingWorkspace,
    store: SessionStore,
    record_path: Path,
    bound_image: str,
    candidate_root: Path,
    candidate_rel: Path,
    candidate_bytes: bytes,
    test_sha256: str,
    deadline: float,
) -> RegressionValidationReport:
    try:
        before_fp = workspace_fingerprint(workspace.path, deadline=deadline)
    except (WorkspaceSnapshotError, TimeoutError, ValueError) as exc:
        timed_out = isinstance(exc, TimeoutError) or time.monotonic() >= deadline
        status, reason = _classify_outcome(None, None, changed=False, timed_out=timed_out)
        report = RegressionValidationReport(
            status=status,
            reason=reason,
            session_id=workspace.session_id,
            base_sha=workspace.base_sha,
            workspace_sha256="",
            test_sha256=test_sha256,
        )
        _record_validation_metadata(store, record_path, report)
        return report

    runner = DockerTestRunner(bound_image)
    base_result: TestResult | None = None
    repaired_result: TestResult | None = None
    changed = False
    timed_out = False
    snapshot_failed = False

    with tempfile.TemporaryDirectory(prefix="selfforge-regression-") as tmp_dir:
        scratch = Path(tmp_dir)
        base_snapshot = scratch / "base_snapshot"
        repaired_snapshot = scratch / "repaired_snapshot"
        try:
            _stage_base_snapshot(
                workspace,
                base_snapshot,
                scratch_root=scratch,
                deadline=deadline,
            )
            _stage_workspace(workspace.path, repaired_snapshot, deadline, None)
            if _check_unchanged(
                workspace.path,
                before_fp,
                candidate_root,
                candidate_rel,
                test_sha256,
                deadline=deadline,
            ):
                changed = True
            else:
                base_result = _run_snapshot_test(
                    runner,
                    base_snapshot,
                    candidate_bytes,
                    deadline=deadline,
                )
                if base_result.status != "timeout":
                    repaired_result = _run_snapshot_test(
                        runner,
                        repaired_snapshot,
                        candidate_bytes,
                        deadline=deadline,
                    )
                    if repaired_result.status != "timeout" and _check_unchanged(
                        workspace.path,
                        before_fp,
                        candidate_root,
                        candidate_rel,
                        test_sha256,
                        deadline=deadline,
                    ):
                        changed = True
        except (_StageTimedOut, TimeoutError):
            timed_out = True
        except (
            WorktreeError,
            WorkspaceSnapshotError,
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            if time.monotonic() >= deadline or "timed out" in str(exc).lower():
                timed_out = True
            # Missing evidence must never reuse the successful runner statuses.
            # Keep those summaries for diagnostics, but revoke confirmation.
            snapshot_failed = True

    if time.monotonic() >= deadline:
        timed_out = True

    status, reason = _classify_outcome(
        base_result,
        repaired_result,
        changed=changed,
        timed_out=timed_out,
    )
    if snapshot_failed and not timed_out:
        status, reason = "error", "snapshot_error"
    report = RegressionValidationReport(
        status=status,
        reason=reason,
        session_id=workspace.session_id,
        base_sha=workspace.base_sha,
        workspace_sha256=before_fp,
        test_sha256=test_sha256,
        base_result=_summarize_result(base_result) if base_result is not None else None,
        repaired_result=_summarize_result(repaired_result) if repaired_result is not None else None,
    )
    _record_validation_metadata(store, record_path, report)
    return report
