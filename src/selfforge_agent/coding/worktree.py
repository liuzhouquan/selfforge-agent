"""Temporary detached Git worktrees for base, head, and repair checks."""

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class WorktreeError(RuntimeError):
    """Git could not prepare or clean a temporary worktree."""


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    if result.returncode:
        raise WorktreeError(result.stderr.strip() or "Git worktree operation failed")
    return result.stdout


@contextmanager
def detached_worktree(repo: Path, revision: str, destination: Path) -> Iterator[Path]:
    git(repo, "worktree", "add", "--detach", str(destination), revision)
    try:
        yield destination
    finally:
        git(repo, "worktree", "remove", "--force", str(destination))
