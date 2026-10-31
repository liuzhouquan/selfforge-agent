"""Regression coverage for delivery boundaries found during branch review."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import (
    DeliveryError,
    commit_workspace,
    create_pull_request,
    revert_workspace,
)
from selfforge_agent.runtime.fingerprint import workspace_fingerprint
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace, CodingWorkspaceManager


@pytest.fixture
def workspace(demo_repo: tuple[Path, str, str], tmp_path: Path) -> CodingWorkspace:
    return CodingWorkspaceManager(tmp_path / "managed").create(demo_repo[0], "deliverysafety")


def test_commit_rejects_pre_staged_protected_file(workspace: CodingWorkspace) -> None:
    before = git(workspace.path, "rev-parse", "HEAD")
    (workspace.path / ".env").write_text("SECRET=private\n")
    git(workspace.path, "add", ".env")
    (workspace.path / "calculator.py").write_text("VALUE = 2\n")
    with pytest.raises(DeliveryError, match="Protected files are already staged"):
        commit_workspace(workspace, "safe change")
    assert git(workspace.path, "rev-parse", "HEAD") == before
    assert ".env" not in git(workspace.path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert ".env" in git(workspace.path, "diff", "--cached", "--name-only").splitlines()


def test_commit_uses_literal_paths(workspace: CodingWorkspace) -> None:
    (workspace.path / ".env").write_text("SECRET=private\n")
    (workspace.path / ":(top).env").write_text("ordinary literal filename\n")
    result = commit_workspace(workspace, "literal path")
    assert ":(top).env" in result.files_committed
    assert ".env" not in git(workspace.path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_commit_rejects_rename_from_protected_path(workspace: CodingWorkspace) -> None:
    (workspace.path / ".env").write_text("SECRET=already-tracked\n")
    git(workspace.path, "add", ".env")
    git(workspace.path, "commit", "-qm", "existing tracked credential fixture")
    before = git(workspace.path, "rev-parse", "HEAD")
    git(workspace.path, "mv", ".env", "public.txt")
    with pytest.raises(DeliveryError, match="Protected files are already staged"):
        commit_workspace(workspace, "must not hide the protected source of a rename")
    assert git(workspace.path, "rev-parse", "HEAD") == before


def test_revert_unlinks_symlink_and_keeps_tracked_target(workspace: CodingWorkspace) -> None:
    target = workspace.path / "calculator.py"
    original = target.read_bytes()
    target.write_text("modified\n")
    link = workspace.path / "temporary-link.py"
    link.symlink_to("calculator.py")
    result = revert_workspace(workspace)
    assert target.read_bytes() == original
    assert not link.is_symlink()
    assert "temporary-link.py" in result.reverted_files


def test_revert_handles_staged_addition_and_rename(workspace: CodingWorkspace) -> None:
    original = (workspace.path / "calculator.py").read_bytes()
    git(workspace.path, "mv", "calculator.py", "renamed.py")
    (workspace.path / "added.py").write_text("new = True\n")
    git(workspace.path, "add", "added.py")
    revert_workspace(workspace)
    assert (workspace.path / "calculator.py").read_bytes() == original
    assert not (workspace.path / "added.py").exists()
    assert not (workspace.path / "renamed.py").exists()
    assert git(workspace.path, "status", "--porcelain") == ""


def test_revert_must_save_invalidation_before_writes(
    workspace: CodingWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SessionStore(tmp_path / "session", workspace.session_id)
    target = workspace.path / "calculator.py"
    target.write_text("must remain if logging fails\n")

    def failed_record(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session, "record_verification", failed_record)
    with pytest.raises(OSError, match="disk full"):
        revert_workspace(workspace, session)
    assert target.read_text() == "must remain if logging fails\n"


def _remote(workspace: CodingWorkspace, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-q")
    git(workspace.repository, "remote", "add", "origin", str(remote))


@pytest.mark.parametrize("committed", [False, True])
def test_pr_does_not_claim_stale_or_uncommitted_evidence(
    workspace: CodingWorkspace, tmp_path: Path, committed: bool
) -> None:
    _remote(workspace, tmp_path)
    target = workspace.path / "calculator.py"
    target.write_text("VALUE = 1\n")
    commit_workspace(workspace, "first change")
    session = SessionStore(tmp_path / "session", workspace.session_id)
    if committed:
        session.record_verification(True, workspace_sha256=workspace_fingerprint(workspace.path))
        target.write_text("VALUE = 2\n")
        commit_workspace(workspace, "untested change")
    else:
        target.write_text("VALUE = 2\n")
        session.record_verification(True, workspace_sha256=workspace_fingerprint(workspace.path))
    with patch("selfforge_agent.runtime.delivery._run_gh", side_effect=[
        "[]", "https://github.com/owner/repo/pull/42\n",
    ]) as run:
        result = create_pull_request(workspace, "PR", session_store=session)
    args = run.call_args_list[-1].args[0]
    body = args[args.index("--body") + 1]
    assert "Unverified" in body
    assert result.commit_sha in body


def test_pr_retry_recovers_creation_after_response_loss(
    workspace: CodingWorkspace, tmp_path: Path
) -> None:
    _remote(workspace, tmp_path)
    (workspace.path / "calculator.py").write_text("VALUE = 1\n")
    commit_workspace(workspace, "change")
    existing = json.dumps([{"url": "https://github.com/owner/repo/pull/42", "number": 42,
                            "title": "Original PR", "baseRefName": "main"}])
    created = False

    def github(args, **kwargs):
        nonlocal created
        if args[:2] == ["pr", "list"]:
            return existing if created else "[]"
        if created:
            raise DeliveryError("PR already exists")
        created = True
        raise DeliveryError("response lost")

    with patch("selfforge_agent.runtime.delivery._run_gh", side_effect=github) as run:
        with pytest.raises(DeliveryError, match="response lost"):
            create_pull_request(workspace, "PR", base_branch="main")
        result = create_pull_request(workspace, "Retry PR", base_branch="main")
    assert result.pr_number == 42
    assert result.title == "Original PR"
    assert sum(call.args[0][:2] == ["pr", "create"] for call in run.call_args_list) == 1


@pytest.mark.parametrize("entry", ["ignored-input.py", "empty-dir", "__pycache__"])
def test_pr_evidence_checks_inputs_omitted_from_commit(
    workspace: CodingWorkspace, tmp_path: Path, entry: str
) -> None:
    _remote(workspace, tmp_path)
    (workspace.path / ".gitignore").write_text("ignored-input.py\n__pycache__/\n")
    commit_workspace(workspace, "ignore local data")
    path = workspace.path / entry
    if entry.endswith(".py"):
        path.write_text("LOCAL_TEST_INPUT = True\n")
    else:
        path.mkdir()
        if entry == "__pycache__":
            (path / "cache.pyc").write_bytes(b"discarded cache")
    session = SessionStore(tmp_path / "session", workspace.session_id)
    session.record_verification(True, workspace_sha256=workspace_fingerprint(workspace.path))
    with patch("selfforge_agent.runtime.delivery._run_gh", side_effect=[
        "[]", "https://github.com/owner/repo/pull/42\n",
    ]) as run:
        create_pull_request(workspace, "PR", session_store=session)
    args = run.call_args_list[-1].args[0]
    body = args[args.index("--body") + 1]
    assert ("Unverified" in body) is (entry != "__pycache__")
    if entry == "__pycache__":
        assert "Verified (tests passed with workspace content match)" in body
