"""Delivery workflow: commit worktree changes and create pull requests."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import (
    CommitResult,
    DeliveryError,
    PRResult,
    commit_workspace,
    create_pull_request,
)
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Author"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "author@example.com"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "Initial commit"], check=True)
    return path


def test_commit_workspace_success_and_protected_file_exclusion(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondelivery1")

    # Modify tracked file
    (workspace.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    # Add new safe file
    (workspace.path / "feature.py").write_text("def run(): pass\n", encoding="utf-8")
    # Add protected files that must NOT be staged or committed
    (workspace.path / ".env").write_text("SECRET=123\n", encoding="utf-8")
    (workspace.path / ".venv").mkdir()
    (workspace.path / ".venv" / "pip.py").write_text("pass\n", encoding="utf-8")

    result = commit_workspace(
        workspace,
        "feat: update value and add feature",
        author_name="Committer",
        author_email="committer@example.com",
    )

    assert isinstance(result, CommitResult)
    assert result.session_id == "sessiondelivery1"
    assert result.branch == "selfforge/sessiondelivery1"
    assert result.message == "feat: update value and add feature"
    assert set(result.files_committed) == {"app.py", "feature.py"}
    assert result.commit_sha == git(workspace.path, "rev-parse", "HEAD").strip()

    # Verify git author
    author = git(workspace.path, "log", "-1", "--format=%an <%ae>").strip()
    assert author == "Committer <committer@example.com>"

    # Verify protected file remained untracked and was not committed
    tracked_files = git(workspace.path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert ".env" not in tracked_files
    assert ".venv/pip.py" not in tracked_files
    assert "app.py" in tracked_files
    assert "feature.py" in tracked_files


def test_commit_workspace_rejects_empty_message(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliveryempty")
    (workspace.path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")

    with pytest.raises(DeliveryError, match="Commit message must not be empty"):
        commit_workspace(workspace, "   ")


def test_commit_workspace_rejects_clean_workspace(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliveryclean")

    with pytest.raises(DeliveryError, match="No changes to commit"):
        commit_workspace(workspace, "feat: no changes")


def test_create_pull_request_rejects_uncommitted_or_no_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliverynocommit")

    with pytest.raises(DeliveryError, match="No commits ahead of base"):
        create_pull_request(workspace, title="PR title")


def test_create_pull_request_rejects_missing_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliverynoremote")
    (workspace.path / "app.py").write_text("VALUE = 5\n", encoding="utf-8")
    commit_workspace(workspace, "feat: new value")

    with pytest.raises(DeliveryError, match="Remote 'origin' does not exist"):
        create_pull_request(workspace, title="PR title", remote="origin")


def test_create_pull_request_success_with_evidence_and_no_force(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    # Setup bare remote repository
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main", "-q"], check=True)

    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliverysuccess")
    (workspace.path / "app.py").write_text("VALUE = 10\n", encoding="utf-8")
    commit_workspace(workspace, "feat: update to 10")

    # Record verified session evidence
    session = SessionStore(manager.session_root(repo, "sessiondeliverysuccess"), "sessiondeliverysuccess")
    session.record_verification(True, workspace_sha256="a" * 64)

    with patch("selfforge_agent.runtime.delivery._run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/owner/repo/pull/42\n"
        pr_result = create_pull_request(
            workspace,
            title="Update value to 10",
            body="Task completed with verified test evidence.",
            session_store=session,
            remote="origin",
        )

        assert isinstance(pr_result, PRResult)
        assert pr_result.pr_url == "https://github.com/owner/repo/pull/42"
        assert pr_result.pr_number == 42
        assert pr_result.branch == workspace.branch
        assert pr_result.title == "Update value to 10"

        # Verify remote received the pushed branch
        remote_branches = git(bare_remote, "branch", "--list").split()
        assert workspace.branch in remote_branches

        # Verify gh call arguments
        mock_gh.assert_called_once()
        cmd_args = mock_gh.call_args[0][0]
        assert "pr" in cmd_args and "create" in cmd_args
        assert "--title" in cmd_args
        assert "Update value to 10" in cmd_args
        # Check that verification note is in body
        assert "--body" in cmd_args
        body_idx = cmd_args.index("--body") + 1
        assert "Verified" in cmd_args[body_idx]
        assert "sessiondeliverysuccess" in cmd_args[body_idx]


def test_create_pull_request_handles_push_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)

    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliverypushfail")
    (workspace.path / "app.py").write_text("VALUE = 20\n", encoding="utf-8")
    commit_workspace(workspace, "feat: update to 20")

    # Point origin to invalid path so push fails
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", "/nonexistent/remote.git"], check=True)

    with pytest.raises(DeliveryError, match="Failed to push branch to remote"):
        create_pull_request(workspace, title="PR title")


def test_create_pull_request_handles_gh_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main", "-q"], check=True)

    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "sessiondeliveryghfail")
    (workspace.path / "app.py").write_text("VALUE = 30\n", encoding="utf-8")
    commit_workspace(workspace, "feat: update to 30")

    with patch("selfforge_agent.runtime.delivery._run_gh") as mock_gh:
        mock_gh.side_effect = DeliveryError("gh: GraphQL: Resource not accessible by integration")
        with pytest.raises(DeliveryError, match="Resource not accessible"):
            create_pull_request(workspace, title="PR title")


def test_web_delivery_endpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main", "-q"], check=True)

    workspaces_mgr = CodingWorkspaceManager(tmp_path / "workspaces")
    jobs_mgr = CodingJobManager(root=tmp_path / "jobs")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", workspaces_mgr)
    monkeypatch.setattr(api_module, "_CODING_JOBS", jobs_mgr)

    workspace = workspaces_mgr.create(repo, "sessionwebdelivery")
    (workspace.path / "app.py").write_text("VALUE = 100\n", encoding="utf-8")

    client = TestClient(api_module.app)

    # 1. Commit endpoint
    resp = client.post(
        f"/api/code/workspaces/{workspace.session_id}/commit",
        json={"repository": str(repo), "message": "feat: web commit test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == workspace.session_id
    assert data["branch"] == workspace.branch
    assert "app.py" in data["files_committed"]
    assert len(data["commit_sha"]) == 40

    # 2. PR endpoint with mocked gh
    with patch("selfforge_agent.runtime.delivery._run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/owner/repo/pull/99\n"
        pr_resp = client.post(
            f"/api/code/workspaces/{workspace.session_id}/pr",
            json={
                "repository": str(repo),
                "title": "Web PR Test",
                "body": "Testing Web PR creation",
                "remote": "origin",
            },
        )
        assert pr_resp.status_code == 200, pr_resp.text
        pr_data = pr_resp.json()
        assert pr_data["pr_url"] == "https://github.com/owner/repo/pull/99"
        assert pr_data["pr_number"] == 99
        assert pr_data["title"] == "Web PR Test"


def test_cli_workspaces_diff_commit_and_pr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path) -> None:
    from selfforge_agent.cli import main

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    repo = _init_repo(tmp_path / "repo")
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main", "-q"], check=True)

    manager = CodingWorkspaceManager()
    workspace = manager.create(repo, "sessionclidelivery")
    (workspace.path / "app.py").write_text("VALUE = 555\n", encoding="utf-8")

    # Test workspaces diff
    monkeypatch.setattr("sys.argv", ["selfforge-agent", "workspaces", "diff", "sessionclidelivery", "--repo", str(repo)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "app.py" in captured.out

    # Test workspaces commit
    monkeypatch.setattr(
        "sys.argv",
        ["selfforge-agent", "workspaces", "commit", "sessionclidelivery", "--repo", str(repo), "--message", "feat: cli commit"],
    )
    assert main() == 0
    captured = capsys.readouterr()
    assert "Committed" in captured.out
    assert "feat: cli commit" in captured.out

    # Test workspaces pr
    with patch("selfforge_agent.runtime.delivery._run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/owner/repo/pull/77\n"
        monkeypatch.setattr(
            "sys.argv",
            ["selfforge-agent", "workspaces", "pr", "sessionclidelivery", "--repo", str(repo), "--title", "CLI PR title"],
        )
        assert main() == 0
        captured = capsys.readouterr()
        assert "Created Pull Request: https://github.com/owner/repo/pull/77" in captured.out
        assert "PR #77" in captured.out
