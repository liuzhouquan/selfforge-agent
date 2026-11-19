"""Security and hardening tests for workspace diff, locking, and lifecycle (SF-WS-09)."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

import pytest

from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.workspace import (
    CodingWorkspace,
    CodingWorkspaceManager,
    WorkspaceError,
    try_workspace_lock,
    unlock_workspace,
    workspace_lock_path,
)
from selfforge_agent.runtime.workspace_diff import (
    MAX_FILE_BYTES,
    MAX_TOTAL_DIFF_BYTES,
    workspace_diff,
)


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    (repo / "README.md").write_text("Hello\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial commit")
    return repo


class TestWorkspaceDiffHardening:
    """Tests for workspace diff bounded output, binary detection, and input validation."""

    def test_untracked_binary_file_with_null_bytes_is_redacted(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "diffbin1")

        # Create an untracked file containing null bytes (binary content)
        bin_file = ws.path / "data.bin"
        bin_file.write_bytes(b"header\x00binary\x00data\xff\xfe")

        diff = workspace_diff(ws)
        file_diff = next((f for f in diff["files"] if f["path"] == "data.bin"), None)
        assert file_diff is not None
        assert file_diff["status"] == "untracked"
        assert file_diff["patch"] == "[Untracked binary file omitted]"
        assert "\x00" not in file_diff["patch"]

    def test_untracked_bounded_read_prevents_oom_on_large_file(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "diffoom1")

        # Create an untracked file that exceeds MAX_FILE_BYTES
        big_file = ws.path / "large.txt"
        big_file.write_text("A" * (MAX_FILE_BYTES + 5000), encoding="utf-8")

        diff = workspace_diff(ws)
        file_diff = next((f for f in diff["files"] if f["path"] == "large.txt"), None)
        assert file_diff is not None
        assert file_diff["patch"] == "[Untracked non-text, symlink, or large file omitted]"

    def test_cumulative_diff_budget_exhaustion_truncates_output(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "diffbudget1")

        # Create several files whose cumulative diff exceeds MAX_TOTAL_DIFF_BYTES
        for i in range(15):
            f = ws.path / f"file_{i:02d}.txt"
            f.write_text((f"line_{i:02d}_" * 10 + "\n") * 300, encoding="utf-8")

        diff = workspace_diff(ws)
        assert len(diff["files"]) > 0
        total_patch_bytes = sum(len(f["patch"].encode("utf-8")) for f in diff["files"])
        assert total_patch_bytes <= MAX_TOTAL_DIFF_BYTES + 1024
        exhausted = [f for f in diff["files"] if "total diff budget reached" in f["patch"]]
        assert len(exhausted) > 0

    def test_tracked_binary_diff_is_sanitized(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "difftrackbin1")

        tracked_bin = ws.path / "app.png"
        tracked_bin.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        git(ws.path, "add", "app.png")
        git(ws.path, "commit", "-m", "add binary")

        # Modify tracked binary file
        tracked_bin.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR_MODIFIED\x00\x00")

        diff = workspace_diff(ws)
        file_diff = next((f for f in diff["files"] if f["path"] == "app.png"), None)
        assert file_diff is not None
        assert file_diff["status"] == "tracked"
        assert "\x00" not in file_diff["patch"]
        assert "Binary file diff omitted" in file_diff["patch"] or "Binary files" in file_diff["patch"]

    def test_workspace_diff_validates_base_sha(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "diffsha1")

        bad_ws = CodingWorkspace(
            repository=ws.repository,
            path=ws.path,
            branch=ws.branch,
            base_sha="--name-only",  # invalid sha, option injection
            session_id=ws.session_id,
        )
        with pytest.raises(ValueError, match="Invalid base commit SHA"):
            workspace_diff(bad_ws)


class TestWorkspaceLockHardening:
    """Tests for workspace file locking flags, symlink defense, and safe release."""

    def test_try_workspace_lock_sets_cloexec_and_permission_0600(self, test_repo: Path) -> None:
        fd = try_workspace_lock(test_repo)
        assert fd is not None
        try:
            # Must have FD_CLOEXEC set so child processes do not inherit lock
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            assert flags & fcntl.FD_CLOEXEC != 0

            # Lock file must have 0o600 permissions
            lock_path = workspace_lock_path(test_repo)
            mode = stat.S_IMODE(lock_path.stat().st_mode)
            assert mode == 0o600
        finally:
            unlock_workspace(fd)

    def test_try_workspace_lock_rejects_symlink_lock_file(self, test_repo: Path, tmp_path: Path) -> None:
        locks_root = tmp_path / "locks"
        locks_root.mkdir(parents=True, mode=0o700)
        lock_file = workspace_lock_path(test_repo, root=locks_root)

        # Attacker creates a symlink in the locks directory
        target = tmp_path / "target_file"
        target.write_text("protected data\n")
        lock_file.symlink_to(target)

        # try_workspace_lock must fail-closed and reject the symlink
        with pytest.raises(WorkspaceError, match="symlink"):
            try_workspace_lock(test_repo, root=locks_root)

    def test_unlock_workspace_handles_invalid_or_closed_fd_safely(self) -> None:
        # Should not raise exception when unlocking an already closed fd
        devnull = os.open("/dev/null", os.O_RDONLY)
        os.close(devnull)
        unlock_workspace(devnull)


class TestWorkspaceLifecycleAndPruneHardening:
    """Tests for workspace removal under lock, branch deletion, and dangling symlinks."""

    def test_remove_locked_workspace_is_rejected(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "lockrm1")

        # An active job or process holds the workspace lock via manager.guard
        with manager.guard(ws.path), pytest.raises(WorkspaceError, match="[Ll]ocked"), manager.guard(ws.path):
            manager.remove(test_repo, "lockrm1")

    def test_remove_cleans_up_worktree_branch(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        ws = manager.create(test_repo, "bclean1")
        branch_name = ws.branch

        # Verify branch exists in git repository
        branches = git(test_repo, "branch", "--list", branch_name).strip()
        assert branch_name in branches

        # Remove workspace with delete_branch=True
        manager.remove(test_repo, "bclean1", delete_branch=True)

        # Verify branch is deleted and not orphaned
        branches_after = git(test_repo, "branch", "--list", branch_name).strip()
        assert branches_after == ""

    def test_create_rejects_dangling_symlink_path_and_metadata(self, test_repo: Path, tmp_path: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        paths = manager._paths(test_repo, "symlinkpath1")
        ws_path, meta_path, _ = paths

        ws_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a dangling symlink for the workspace path
        ws_path.symlink_to(tmp_path / "non_existent_dir")

        with pytest.raises(WorkspaceError, match="already exists|invalid"):
            manager.create(test_repo, "symlinkpath1")

        ws_path.unlink()
        # Test dangling symlink on metadata path
        meta_path.symlink_to(tmp_path / "non_existent_meta.json")
        with pytest.raises(WorkspaceError, match="already exists|invalid"):
            manager.create(test_repo, "symlinkpath1")

    def test_paths_fingerprint_normalized_with_trailing_slash(self, test_repo: Path) -> None:
        manager = CodingWorkspaceManager(root=test_repo.parent / "worktrees")
        repo_with_slash = Path(str(test_repo) + "/")
        p1 = manager._paths(test_repo, "norm1")
        p2 = manager._paths(repo_with_slash, "norm1")
        assert p1 == p2


def test_diff_uses_literal_tracked_paths_without_selecting_protected_files(test_repo):
    (test_repo / ':!safe.txt').write_text('old')
    (test_repo / '.env').write_text('old')
    git(test_repo, '--literal-pathspecs', 'add', '--', ':!safe.txt', '.env')
    git(test_repo, 'commit', '-m', 'Literal path fixture')
    manager = CodingWorkspaceManager(test_repo.parent / 'worktrees')
    ws = manager.create(test_repo, 'literalpaths')
    (ws.path / ':!safe.txt').write_text('new literal content')
    (ws.path / '.env').write_text('PRIVATE_FIXTURE')
    report = workspace_diff(ws)
    assert 'PRIVATE_FIXTURE' not in str(report)
    patch = next(f['patch'] for f in report['files'] if f['path'] == ':!safe.txt')
    assert 'new literal content' in patch


def test_workspace_lock_does_not_follow_a_replaced_path(test_repo, tmp_path, monkeypatch):
    import selfforge_agent.runtime.workspace as module

    locks = tmp_path / 'locks'
    path = workspace_lock_path(test_repo, root=locks)
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    outside.chmod(0o644)
    original_open = os.open
    swapped = False

    def swap(target, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and (str(target) == str(path) or (str(target) == path.name and 'dir_fd' in kwargs)):
            swapped = True
            path.symlink_to(outside)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, 'open', swap)
    try:
        fd = try_workspace_lock(test_repo, root=locks)
    except (OSError, WorkspaceError):
        pass
    else:
        assert fd is not None
        unlock_workspace(fd)
    assert swapped
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert outside.read_text() == 'unchanged'


def test_diff_rejects_a_file_replaced_by_fifo(test_repo, monkeypatch):
    import threading
    import time

    manager = CodingWorkspaceManager(test_repo.parent / 'worktrees')
    ws = manager.create(test_repo, 'fiforace')
    target = ws.path / 'race.txt'
    target.write_text('original')
    original_open = os.open
    swapped = False
    workers = []

    def produce():
        time.sleep(0.05)
        try:
            fd = original_open(target, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        try:
            os.write(fd, b'FIFO_UNTRUSTED')
        finally:
            os.close(fd)

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and (str(path) == str(target) or (str(path) == target.name and 'dir_fd' in kwargs)):
            swapped = True
            target.unlink()
            os.mkfifo(target)
            worker = threading.Thread(target=produce, daemon=True)
            workers.append(worker)
            worker.start()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', swap)
    report = workspace_diff(ws)
    for worker in workers:
        worker.join(timeout=1)
    assert swapped
    assert 'FIFO_UNTRUSTED' not in str(report)


def test_diff_total_budget_includes_truncation_markers(test_repo, monkeypatch):
    import selfforge_agent.runtime.workspace_diff as module

    manager = CodingWorkspaceManager(test_repo.parent / 'worktrees')
    ws = manager.create(test_repo, 'totalbudget')
    for index in range(3):
        (ws.path / f'file{index}.txt').write_text('x' * 200)
    monkeypatch.setattr(module, 'MAX_TOTAL_DIFF_BYTES', 100)
    report = module.workspace_diff(ws)
    assert sum(len(f['patch'].encode()) for f in report['files']) <= 100
    assert any(f['truncated'] for f in report['files'])
