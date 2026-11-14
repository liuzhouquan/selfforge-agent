"""Fail-closed boundary and safety tests for workspace_diff and delivery (SF-WS-08)."""

import os
from pathlib import Path

import pytest

from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import (
    commit_workspace,
    revert_workspace,
)
from selfforge_agent.runtime.workspace import CodingWorkspace, CodingWorkspaceManager
from selfforge_agent.runtime.workspace_diff import workspace_diff


@pytest.fixture
def workspace(demo_repo: tuple[Path, str, str], tmp_path: Path) -> CodingWorkspace:
    return CodingWorkspaceManager(tmp_path / "managed").create(demo_repo[0], "diffsafety")


def test_workspace_diff_omits_untracked_files_under_symlinked_directory(
    workspace: CodingWorkspace, tmp_path: Path
) -> None:
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("OUTSIDE_SECRET_12345\n", encoding="utf-8")

    # 1. Top-level symlink directory
    symlink_dir = workspace.path / "sym_link_dir"
    symlink_dir.symlink_to(outside_dir)

    # 2. Nested symlink directory
    nested_parent = workspace.path / "nested"
    nested_parent.mkdir()
    nested_symlink = nested_parent / "outside_sym"
    nested_symlink.symlink_to(outside_dir)

    diff = workspace_diff(workspace)
    diff_str = str(diff)
    assert "OUTSIDE_SECRET_12345" not in diff_str
    for file_entry in diff["files"]:
        if "sym_link_dir" in file_entry["path"] or "outside_sym" in file_entry["path"]:
            assert "[Untracked non-text, symlink, or large file omitted]" in file_entry["patch"]


def test_workspace_diff_handles_unreadable_and_oserror_files(
    workspace: CodingWorkspace
) -> None:
    unreadable = workspace.path / "unreadable.py"
    unreadable.write_text("CANNOT_READ\n", encoding="utf-8")
    unreadable.chmod(0o000)

    try:
        diff = workspace_diff(workspace)
        files = {item["path"]: item for item in diff["files"]}
        assert "unreadable.py" in files
        assert files["unreadable.py"]["patch"] in (
            "[Untracked unreadable file omitted]",
            "[Untracked non-text, symlink, or large file omitted]",
        )
    finally:
        unreadable.chmod(0o644)


def test_revert_workspace_removes_untracked_broken_and_directory_symlinks(
    workspace: CodingWorkspace, tmp_path: Path
) -> None:
    outside_dir = tmp_path / "outside_for_revert"
    outside_dir.mkdir()

    dir_link = workspace.path / "untracked_dir_link"
    dir_link.symlink_to(outside_dir)

    broken_link = workspace.path / "untracked_broken_link"
    broken_link.symlink_to("nonexistent.target")

    assert dir_link.is_symlink()
    assert broken_link.is_symlink()

    result = revert_workspace(workspace)
    assert not dir_link.exists()
    assert not dir_link.is_symlink()
    assert not broken_link.is_symlink()
    assert "untracked_dir_link" in result.reverted_files
    assert "untracked_broken_link" in result.reverted_files


def test_commit_workspace_filters_untracked_fifos(
    workspace: CodingWorkspace
) -> None:
    fifo_path = workspace.path / "bad_pipe"
    os.mkfifo(fifo_path)

    (workspace.path / "calculator.py").write_text("VALUE = 99\n", encoding="utf-8")

    result = commit_workspace(workspace, "valid commit excluding fifo")
    assert "calculator.py" in result.files_committed
    assert "bad_pipe" not in result.files_committed
    assert "bad_pipe" not in git(workspace.path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
