"""Temporary detached Git worktrees for base, head, and repair checks."""

import math
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic

from selfforge_agent.core.process import run_bounded

MAX_ARG_CHARS = 65_536  # 64 KiB
MAX_GIT_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_ALLOWED_BOUNDED_BYTES = 50 * 1024 * 1024  # 50 MiB


class WorktreeError(RuntimeError):
    """Git could not prepare or clean a temporary worktree."""


def _validate_deadline(deadline: object) -> float | None:
    if deadline is None:
        return None
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        raise TypeError(f"Deadline must be a finite number, got {type(deadline).__name__}")
    if not math.isfinite(deadline):
        raise ValueError(f"Deadline must be a finite number: {deadline!r}")
    return float(deadline)


def _validate_repo(repo: object) -> Path:
    if not isinstance(repo, (Path, str)):
        raise TypeError(f"Repository path must be a Path or str, got {type(repo).__name__}")
    p = Path(repo)
    if not p.exists():
        raise WorktreeError(f"Repository path does not exist: {p}")
    if p.is_symlink():
        raise WorktreeError(f"Repository path must not be a symlink: {p}")
    if not p.is_dir():
        raise WorktreeError(f"Repository path is not a directory: {p}")
    return p


def _validate_args(args: tuple[object, ...]) -> list[str]:
    if not args:
        raise ValueError("At least one Git subcommand or argument is required")
    clean: list[str] = []
    for arg in args:
        if not isinstance(arg, str):
            raise TypeError(f"Git argument must be a string, got {type(arg).__name__}")
        if "\0" in arg:
            raise WorktreeError(f"Git argument must not contain null bytes: {arg!r}")
        if len(arg) > MAX_ARG_CHARS:
            raise WorktreeError(f"Git argument exceeds maximum length of {MAX_ARG_CHARS} characters")
        clean.append(arg)
    return clean


def git(repo: Path, *args: str, deadline: float | None = None) -> str:
    repo_path = _validate_repo(repo)
    clean_args = _validate_args(args)
    dl = _validate_deadline(deadline)

    if dl is not None:
        remaining = dl - monotonic()
        if remaining <= 0:
            raise TimeoutError("Git operation budget exhausted")
        timeout = min(30.0, remaining)
    else:
        timeout = 30.0

    try:
        result = run_bounded(
            ["git", "-C", str(repo_path), *clean_args], timeout=timeout,
            max_bytes=MAX_GIT_OUTPUT_BYTES,
        )
    except (OSError, TimeoutError) as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    if result.stdout_limited or result.stderr_limited:
        raise WorktreeError(f"Git output exceeded maximum limit of {MAX_GIT_OUTPUT_BYTES} bytes")
    if result.returncode:
        raise WorktreeError(result.stderr.decode("utf-8", errors="replace").strip()
                            or "Git worktree operation failed")
    return result.stdout.decode("utf-8")


def git_bounded(
    repo: Path,
    *args: str,
    max_bytes: int,
    deadline: float | None = None,
) -> tuple[str, bool]:
    """Read at most max_bytes of Git stdout, stopping a larger patch early."""
    repo_path = _validate_repo(repo)
    clean_args = _validate_args(args)

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise TypeError("Git output limit must be an integer")
    if max_bytes < 1:
        raise ValueError("Git output limit must be positive")
    if max_bytes > MAX_ALLOWED_BOUNDED_BYTES:
        raise ValueError(f"Git output limit exceeds maximum allowed ({MAX_ALLOWED_BOUNDED_BYTES})")

    dl = _validate_deadline(deadline)
    effective_deadline = dl if dl is not None else monotonic() + 30.0

    remaining = effective_deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Git operation budget exhausted")

    try:
        result = run_bounded(
            ["git", "-C", str(repo_path), *clean_args], timeout=min(3600, remaining),
            max_bytes=max_bytes, stderr_bytes=4096,
        )
    except (OSError, TimeoutError) as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    if result.stderr_limited:
        raise WorktreeError("Git error output exceeded maximum limit")
    if result.returncode and not result.stdout_limited:
        raise WorktreeError(result.stderr.decode("utf-8", errors="replace").strip()
                            or "Git worktree operation failed")
    return result.stdout.decode(
        "utf-8", errors="replace" if result.stdout_limited else "strict"
    ), result.stdout_limited


@contextmanager
def detached_worktree(
    repo: Path,
    revision: str,
    destination: Path,
    *,
    deadline: float | None = None,
) -> Iterator[Path]:
    # Reserve part of the caller's budget for removing temporary Git metadata.
    repo_path = _validate_repo(repo)

    if not isinstance(revision, str):
        raise TypeError(f"Revision must be a string, got {type(revision).__name__}")
    rev = revision.strip()
    if not rev:
        raise ValueError("Revision must be a non-empty string")
    if "\0" in rev:
        raise ValueError("Revision must not contain null bytes")
    if rev.startswith("-"):
        raise ValueError(f"Revision must not start with a hyphen: {rev!r}")

    if not isinstance(destination, (Path, str)):
        raise TypeError(f"Destination must be a Path or str, got {type(destination).__name__}")
    dest = Path(destination)
    if "\0" in str(dest):
        raise ValueError("Destination path must not contain null bytes")
    if dest.is_symlink():
        raise WorktreeError(f"Worktree destination must not be a symlink: {dest}")

    try:
        dest_resolved = dest.resolve()
        repo_resolved = repo_path.resolve()
        if dest_resolved == repo_resolved:
            raise WorktreeError("Worktree destination must not be the repository root")
        git_dir = (repo_resolved / ".git").resolve()
        if dest_resolved == git_dir or git_dir in dest_resolved.parents:
            raise WorktreeError("Worktree destination must not be inside .git directory")
    except OSError:
        pass

    dl = _validate_deadline(deadline)
    if dl is not None:
        budget = dl - monotonic()
        if budget <= 0.25:
            raise TimeoutError("Git operation budget exhausted for worktree checkout")
        checkout_deadline = dl - 0.25
    else:
        checkout_deadline = None

    existed = dest.exists()
    added = False
    try:
        git(
            repo_path,
            "worktree",
            "add",
            "--detach",
            str(dest),
            "--",
            rev,
            deadline=checkout_deadline,
        )
        added = True
        yield dest
    finally:
        body_failed = sys.exc_info()[0] is not None
        if added or (not existed and dest.exists()):
            try:
                git(repo_path, "worktree", "remove", "--force", str(dest), deadline=deadline)
            except (WorktreeError, OSError, TimeoutError):
                try:
                    git(repo_path, "worktree", "prune", deadline=deadline)
                except (WorktreeError, OSError, TimeoutError):
                    pass
                if not body_failed:
                    raise
