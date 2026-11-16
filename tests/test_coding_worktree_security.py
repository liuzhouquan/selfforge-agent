"""Security, fail-closed input defense, and boundary tests for Git worktree operations."""

import subprocess
from pathlib import Path
from time import monotonic

import pytest

from selfforge_agent.coding.worktree import (
    WorktreeError,
    detached_worktree,
    git,
    git_bounded,
)


@pytest.fixture
def test_git_repo(tmp_path: Path) -> Path:
    """Create a minimal clean Git repository for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "README.md").write_text("initial commit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    return repo


def test_git_command_argument_and_path_defense(test_git_repo: Path, tmp_path: Path) -> None:
    """git() must validate repo path, arguments, and finite positive deadlines."""
    # 1. Non-existent, symlink, or non-directory repo path
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises((WorktreeError, OSError)):
        git(non_existent, "status")

    symlink_repo = tmp_path / "symlink_repo"
    symlink_repo.symlink_to(test_git_repo)
    with pytest.raises(WorktreeError, match="symlink"):
        git(symlink_repo, "status")

    file_repo = tmp_path / "a_file.txt"
    file_repo.write_text("not a dir", encoding="utf-8")
    with pytest.raises(WorktreeError, match="not a directory"):
        git(file_repo, "status")

    # 2. Argument validation: empty args, non-string, null byte, oversized
    with pytest.raises(ValueError, match="At least one"):
        git(test_git_repo)

    with pytest.raises(TypeError):
        git(test_git_repo, None)  # type: ignore[arg-type]

    with pytest.raises(WorktreeError, match="null byte"):
        git(test_git_repo, "status\x00")

    with pytest.raises(WorktreeError, match="exceeds maximum"):
        git(test_git_repo, "log", "a" * 70_000)

    # 3. Deadline validation: reject bool, nan, inf, exhausted
    for bad_deadline in (True, False, "100", None):
        if bad_deadline is None:
            continue
        with pytest.raises((TypeError, ValueError)):
            git(test_git_repo, "status", deadline=bad_deadline)  # type: ignore[arg-type]

    for bad_num in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            git(test_git_repo, "status", deadline=bad_num)

    # Exhausted deadline
    with pytest.raises(TimeoutError, match="exhausted"):
        git(test_git_repo, "status", deadline=monotonic() - 1.0)


def test_git_bounded_input_and_timeout_defense(test_git_repo: Path) -> None:
    """git_bounded() must validate max_bytes, deadline, and handle bounded output."""
    # 1. max_bytes validation: reject bool, non-positive, > 50 MiB, non-int
    for bad_bytes in (True, False, "1024", None):
        with pytest.raises(TypeError):
            git_bounded(test_git_repo, "status", max_bytes=bad_bytes)  # type: ignore[arg-type]

    for bad_val in (0, -1, -100, 60 * 1024 * 1024):
        with pytest.raises(ValueError):
            git_bounded(test_git_repo, "status", max_bytes=bad_val)

    # 2. Deadline validation
    for bad_deadline in (True, False, "100"):
        with pytest.raises(TypeError):
            git_bounded(test_git_repo, "status", max_bytes=1024, deadline=bad_deadline)  # type: ignore[arg-type]

    for bad_num in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            git_bounded(test_git_repo, "status", max_bytes=1024, deadline=bad_num)

    with pytest.raises(TimeoutError, match="exhausted"):
        git_bounded(test_git_repo, "status", max_bytes=1024, deadline=monotonic() - 1.0)

    # 3. Valid bounded execution
    output, truncated = git_bounded(test_git_repo, "log", "-n", "1", max_bytes=10)
    assert len(output.encode("utf-8")) <= 10
    assert truncated is True


def test_detached_worktree_revision_and_destination_defense(test_git_repo: Path, tmp_path: Path) -> None:
    """detached_worktree() must reject option injection, symlinks, and root/git collisions."""
    dest = tmp_path / "wt_dest"

    # 1. Revision validation: empty, null byte, flag injection
    for bad_rev in ("", "   ", None):
        with pytest.raises((ValueError, TypeError)), detached_worktree(test_git_repo, bad_rev, dest):  # type: ignore[arg-type]
            pass

    with pytest.raises(ValueError, match="null byte"), detached_worktree(test_git_repo, "HEAD\x00", dest):
        pass

    for flag_rev in ("--orphan", "-b", "--detach"):
        with pytest.raises(ValueError, match="hyphen"), detached_worktree(test_git_repo, flag_rev, dest):
            pass

    # 2. Destination validation: symlink, collision with repo root or .git
    symlink_dest = tmp_path / "symlink_dest"
    symlink_dest.symlink_to(tmp_path)
    with pytest.raises(WorktreeError, match="symlink"), detached_worktree(test_git_repo, "HEAD", symlink_dest):
        pass

    with pytest.raises(WorktreeError, match="repository root"), detached_worktree(test_git_repo, "HEAD", test_git_repo):
        pass

    git_inner_dest = test_git_repo / ".git" / "worktree_sub"
    with pytest.raises(WorktreeError, match=r"\.git"), detached_worktree(test_git_repo, "HEAD", git_inner_dest):
        pass

    # 3. Budget exhaustion before checkout (deadline <= 0.25s)
    with pytest.raises(TimeoutError, match="exhausted"), detached_worktree(test_git_repo, "HEAD", dest, deadline=monotonic() + 0.1):
        pass


def test_detached_worktree_lifecycle_and_cleanup_fault_tolerance(test_git_repo: Path, tmp_path: Path) -> None:
    """detached_worktree() cleans up on exit and preserves in-flight caller exceptions."""
    dest = tmp_path / "valid_wt"

    # Normal lifecycle
    with detached_worktree(test_git_repo, "HEAD", dest) as tree:
        assert tree.exists()
        assert (tree / "README.md").is_file()
    assert not dest.exists()

    # Exception inside block is preserved and not masked by cleanup
    class CustomCallerError(Exception):
        pass

    with pytest.raises(CustomCallerError), detached_worktree(test_git_repo, "HEAD", dest) as tree:
        assert tree.exists()
        raise CustomCallerError("caller error must not be suppressed")

    assert not dest.exists()
