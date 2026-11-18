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
        exhausted = [f for f in diff["files"] if "Total patch budget exhausted" in f["patch"]]
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
