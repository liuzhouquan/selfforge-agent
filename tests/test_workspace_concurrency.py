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
            cli_main(["workspaces", "remove", "--repo", str(repo), session_id])
        assert exc_remove.value.code != 0

        # Attempt CLI commit while locked
        with pytest.raises(SystemExit) as exc_commit:
            cli_main(["workspaces", "commit", "--repo", str(repo), session_id, "-m", "fix"])
        assert exc_commit.value.code != 0

        # Attempt CLI revert while locked
        with pytest.raises(SystemExit) as exc_revert:
            cli_main(["workspaces", "revert", "--repo", str(repo), session_id])
        assert exc_revert.value.code != 0



def test_custom_storage_roots_do_not_split_workspace_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    manager = CodingWorkspaceManager(root=tmp_path / "custom-worktrees")
    jobs = CodingJobManager(root=tmp_path / "custom-jobs")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with manager.guard(workspace), pytest.raises(ValueError, match="running"), jobs.workspace_guard(workspace):
        pass


@pytest.mark.parametrize("action", ["remove", "revert", "commit", "pr"])
def test_separate_cli_process_cannot_deliver_or_remove_web_owned_workspace(tmp_path, monkeypatch, action):
    import os
    import subprocess
    import sys
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager()
    workspace = manager.create(repo, "crossprocess01")
    jobs = CodingJobManager(root=tmp_path / "custom-job-records")
    args = ["workspaces", action, "--repo", str(repo), workspace.session_id]
    if action == "commit":
        args += ["-m", "blocked commit"]
    if action == "pr":
        args += ["--title", "blocked PR"]
    with jobs.workspace_guard(workspace.path):
        result = subprocess.run([sys.executable, "-m", "selfforge_agent.cli", *args],
                                check=False, capture_output=True, text=True, timeout=10, env=os.environ.copy())
    assert result.returncode == 2
    assert "locked" in result.stderr
    assert workspace.path.is_dir()
    assert (workspace.path / "file.txt").read_text() == "hello\n"
    assert git(workspace.path, "status", "--porcelain") == ""
