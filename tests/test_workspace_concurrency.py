"""Tests for unified workspace concurrency coordination between CLI and Web."""

import multiprocessing
from pathlib import Path

import pytest
from conftest import git

from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.workspace import (
    CodingWorkspaceManager,
    WorkspaceError,
    try_workspace_lock,
    unlock_workspace,
    workspace_guard,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    (path / "file.txt").write_text("hello\n", encoding="utf-8")
    git(path, "add", "file.txt")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Initial")
    return path


def test_try_workspace_lock_and_unlock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    locks_root = tmp_path / "locks"

    fd1 = try_workspace_lock(workspace, root=locks_root)
    assert fd1 is not None

    # Second acquire on the same workspace is rejected
    fd2 = try_workspace_lock(workspace, root=locks_root)
    assert fd2 is None

    # workspace_guard on locked workspace raises WorkspaceError
    with pytest.raises(WorkspaceError, match="locked"), workspace_guard(workspace, root=locks_root):
        pass

    # Unlock fd1
    unlock_workspace(fd1)

    # Now acquire succeeds
    fd3 = try_workspace_lock(workspace, root=locks_root)
    assert fd3 is not None
    unlock_workspace(fd3)


def _holding_worker(workspace: Path, locks_root: Path, ready_event: multiprocessing.Event, stop_event: multiprocessing.Event) -> None:
    fd = try_workspace_lock(workspace, root=locks_root)
    if fd is not None:
        ready_event.set()
        stop_event.wait(timeout=10)
        unlock_workspace(fd)


def test_multiprocess_workspace_lock_and_automatic_cleanup_on_kill(tmp_path: Path) -> None:
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    locks_root = tmp_path / "locks"

    ready = multiprocessing.Event()
    stop = multiprocessing.Event()
    proc = multiprocessing.Process(target=_holding_worker, args=(workspace, locks_root, ready, stop))
    proc.start()

    try:
        assert ready.wait(timeout=5), "Worker failed to acquire lock"
        # While worker holds the lock, main process cannot acquire
        assert try_workspace_lock(workspace, root=locks_root) is None

        # Kill worker abruptly
        proc.kill()
        proc.join(timeout=5)

        # Kernel must release the flock automatically on process death
        fd = try_workspace_lock(workspace, root=locks_root)
        assert fd is not None, "Lock was not automatically released after process death"
        unlock_workspace(fd)
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join()


def test_coding_job_manager_shares_lock_with_workspace_guard(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(root=tmp_path / "worktrees")
    workspace = manager.create(repo, "sessiontestlock01")

    job_mgr = CodingJobManager(root=tmp_path / "jobs")
    # Point locks to the same directory
    job_mgr.locks = manager.locks_root

    # When job manager holds the workspace guard
    with job_mgr.workspace_guard(workspace.path):
        # CodingWorkspaceManager.guard is rejected
        with pytest.raises(WorkspaceError, match="locked"), manager.guard(workspace.path):
            pass

        # Direct try_workspace_lock is rejected
        assert try_workspace_lock(workspace.path, root=manager.locks_root) is None

    # After guard exit, lock can be acquired
    with manager.guard(workspace.path):
        assert try_workspace_lock(workspace.path, root=manager.locks_root) is None


def test_cli_workspaces_rejected_when_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from selfforge_agent.cli import main as cli_main

    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(root=tmp_path / "worktrees")
    monkeypatch.setattr("selfforge_agent.cli.CodingWorkspaceManager", lambda: manager)

    session_id = "clilocktest01"
    workspace = manager.create(repo, session_id)

    # Acquire lock on workspace
    with manager.guard(workspace.path):
        # Attempt CLI remove while locked
        with pytest.raises(SystemExit) as exc_remove:
            cli_main(["workspaces", "remove", "--repo", str(repo), "--session-id", session_id])
        assert exc_remove.value.code != 0

        # Attempt CLI commit while locked
        with pytest.raises(SystemExit) as exc_commit:
            cli_main(["workspaces", "commit", "--repo", str(repo), "--session-id", session_id, "-m", "fix"])
        assert exc_commit.value.code != 0

        # Attempt CLI revert while locked
        with pytest.raises(SystemExit) as exc_revert:
            cli_main(["workspaces", "revert", "--repo", str(repo), "--session-id", session_id])
        assert exc_revert.value.code != 0

