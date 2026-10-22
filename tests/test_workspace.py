"""Coding worktrees stay isolated and are removed only after review."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from selfforge_agent.coding.worktree import WorktreeError
from selfforge_agent.runtime.evaluation import CASES, OfflineFixtureModel
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager, WorkspaceError
from selfforge_agent.runtime.workspace_diff import workspace_diff


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Initial"], check=True)
    return path


def test_worktree_isolated_resume_and_safe_cleanup(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    original = (repo / "app.py").read_text()
    (repo / "app.py").write_text("VALUE = 9\n")  # Uncommitted source changes stay out.
    workspace = manager.create(repo, "abc123")
    assert workspace.path != repo
    assert workspace.path.stat().st_mode & 0o777 == 0o700
    assert workspace.branch == "selfforge/abc123"
    assert (workspace.path / "app.py").read_text() == original
    assert manager.get(repo, "abc123") == workspace
    assert manager.list(repo) == [workspace]
    (workspace.path / "app.py").write_text("VALUE = 2\n")
    with pytest.raises(WorktreeError, match="modified|untracked"):
        manager.remove(repo, "abc123")
    assert workspace.path.exists()
    assert (repo / "app.py").read_text() == "VALUE = 9\n"
    subprocess.run(["git", "-C", str(workspace.path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(workspace.path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Fix"], check=True)
    manager.remove(repo, "abc123")
    assert not workspace.path.exists()
    assert manager.list(repo) == []
    assert subprocess.run(["git", "-C", str(repo), "branch", "--list", workspace.branch],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_worktree_rejects_missing_commit_and_wrong_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    other = _repo(tmp_path / "other")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    with pytest.raises(WorkspaceError, match="Invalid session ID"):
        manager.create(repo, "../escape")
    manager.create(repo, "abc123")
    with pytest.raises(WorkspaceError, match="Unknown Coding workspace"):
        manager.get(other, "abc123")
    with pytest.raises(WorkspaceError, match="already exists"):
        manager.create(repo, "abc123")
    metadata = next((tmp_path / "workspaces").rglob("abc123.json"))
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["repository"] = str(other)
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="metadata does not match"):
        manager.get(repo, "abc123")


def test_worktree_storage_must_not_be_inside_source(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    manager = CodingWorkspaceManager(repo / ".workspaces")
    with pytest.raises(WorkspaceError, match="outside"):
        manager.create(repo, "abc123")


def test_worktree_requires_committed_repository_root(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    with pytest.raises(WorkspaceError, match="initial commit"):
        manager.create(repo, "abc123")
    (repo / "nested").mkdir()
    (repo / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Initial"], check=True)
    with pytest.raises(WorkspaceError, match="repository root"):
        manager.create(repo / "nested", "abc123")


def test_workspace_diff_includes_commits_edits_and_safe_untracked_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    workspace = CodingWorkspaceManager(tmp_path / "workspaces").create(repo, "abc123")
    (workspace.path / "app.py").write_text("VALUE = 2\n")
    subprocess.run(["git", "-C", str(workspace.path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(workspace.path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "First fix"], check=True)
    (workspace.path / "app.py").write_text("VALUE = 3\n")
    (workspace.path / "new_test.py").write_text("assert True\n")
    (workspace.path / ".env").write_text("SECRET=hidden\n")
    (workspace.path / "alias.py").symlink_to(".env")
    result = workspace_diff(workspace)
    files = {item["path"]: item for item in result["files"]}
    assert set(files) == {"app.py", "new_test.py", "alias.py"}
    assert "VALUE = 3" in files["app.py"]["patch"]
    assert "VALUE = 1" in files["app.py"]["patch"]
    assert "+assert True" in files["new_test.py"]["patch"]
    assert "SECRET" not in str(result)
    assert result["omitted_files"] == 1


def test_workspace_diff_caps_large_patches(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    workspace = CodingWorkspaceManager(tmp_path / "workspaces").create(repo, "abc123")
    (workspace.path / "large.py").write_text("x = 1\n" * 5_000)
    result = workspace_diff(workspace)
    assert result["files"][0]["truncated"]
    assert len(result["files"][0]["patch"]) < 25_000


def test_new_sessions_live_outside_repo_and_legacy_sessions_resume(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "source")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    canonical = manager.session_root(repo)
    assert not canonical.is_relative_to(repo)
    SessionStore(canonical, "new123").record_status("running")
    assert manager.session_root(repo, "new123", legacy_roots=(repo,)) == canonical
    SessionStore(repo, "old123").record_status("running")
    assert manager.session_root(repo, "old123", legacy_roots=(repo,)) == repo


def test_cli_creates_lists_and_removes_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    from selfforge_agent import cli

    repo = _repo(tmp_path / "source")
    (repo / "app.py").write_text(CASES[0].source, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(CASES[0].test, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Fixture"], check=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: OfflineFixtureModel(CASES[0]))
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "code", "--repo", str(repo),
                                      "--task", "Fix addition", "--sandbox", "host",
                                      "--trust-local", "--model", "fixture",
                                      "--base-url", "https://example.com/v1"])
    assert cli.main() == 0
    output = capsys.readouterr().out
    session_id = output.split("Session ", 1)[1].split(":", 1)[0]
    workspace = CodingWorkspaceManager().get(repo, session_id)
    assert "return a + b" in (workspace.path / "app.py").read_text()
    assert "return a - b" in (repo / "app.py").read_text()
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "workspaces", "list", "--repo", str(repo)])
    assert cli.main() == 0
    assert session_id in capsys.readouterr().out
    subprocess.run(["git", "-C", str(workspace.path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace.path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Fix"], check=True)
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "workspaces", "remove", session_id,
                                      "--repo", str(repo)])
    assert cli.main() == 0
    assert not workspace.path.exists()
