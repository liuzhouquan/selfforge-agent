"""Tests for workspace dirty state detection and uncommitted file guardrail (SF-WS-03)."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.cli import main
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run([
        "git", "-C", str(path),
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-qm", "Initial",
    ], check=True)
    return path


def test_is_dirty_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "clean_repo")
    is_dirty, sample = CodingWorkspaceManager.check_dirty(repo)
    assert not is_dirty
    assert sample == []
    assert not CodingWorkspaceManager.is_dirty(repo)
    assert CodingWorkspaceManager.dirty_files(repo) == []


def test_is_dirty_tracked_modified_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "modified_repo")
    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    is_dirty, sample = CodingWorkspaceManager.check_dirty(repo)
    assert is_dirty
    assert "app.py" in sample
    assert CodingWorkspaceManager.is_dirty(repo)
    assert "app.py" in CodingWorkspaceManager.dirty_files(repo)


def test_is_dirty_untracked_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "untracked_repo")
    (repo / "untracked.txt").write_text("notes\n", encoding="utf-8")
    is_dirty, sample = CodingWorkspaceManager.check_dirty(repo)
    assert is_dirty
    assert "untracked.txt" in sample
    assert CodingWorkspaceManager.is_dirty(repo)


def test_is_dirty_staged_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "staged_repo")
    (repo / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    assert CodingWorkspaceManager.is_dirty(repo)
    assert "app.py" in CodingWorkspaceManager.dirty_files(repo)


def test_is_dirty_exempt_caches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "cache_repo")
    # Add .gitignore for caches
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.ruff_cache/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run([
        "git", "-C", str(repo),
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-qm", "Ignore caches",
    ], check=True)

    # Create ignored cache directories and files
    pycache = repo / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

    pytest_cache = repo / ".pytest_cache"
    pytest_cache.mkdir()
    (pytest_cache / "nodeids").write_text("test_app\n", encoding="utf-8")

    # Derived caches are safely exempt
    assert not CodingWorkspaceManager.is_dirty(repo)
    assert CodingWorkspaceManager.dirty_files(repo) == []


def test_is_dirty_non_cache_ignored_is_dirty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "ignored_repo")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run([
        "git", "-C", str(repo),
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-qm", "Ignore .env",
    ], check=True)

    # Create user ignored file (.env is NOT a build/pytest cache)
    (repo / ".env").write_text("SECRET=xyz\n", encoding="utf-8")
    assert CodingWorkspaceManager.is_dirty(repo)
    assert ".env" in CodingWorkspaceManager.dirty_files(repo)


def test_cli_direct_mode_rejects_dirty_repo_without_allow_dirty(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _init_repo(tmp_path / "cli_repo")
    (repo / "app.py").write_text("MODIFIED\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    with pytest.raises(SystemExit) as exc:
        main([
            "code",
            "--repo", str(repo),
            "--workspace", "direct",
            "--sandbox", "host",
            "--trust-local",
            "--task", "Fix run()",
        ])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    err = captured.err
    assert "Direct workspace has uncommitted changes" in err or "untracked files" in err
    assert "app.py" in err
    assert "--allow-dirty" in err


def test_cli_direct_mode_allows_dirty_when_flag_passed(tmp_path: Path, monkeypatch) -> None:
    from selfforge_agent.runtime.model import ModelTurn

    repo = _init_repo(tmp_path / "cli_allowed_repo")
    (repo / "app.py").write_text("MODIFIED\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    class ScriptedModel:
        def complete(self, messages, tools, timeout):
            return ModelTurn({"role": "assistant", "content": "Done."}, "stop", "test", "test", None)

    monkeypatch.setattr("selfforge_agent.cli.OpenAIChatAdapter", lambda *args, **kwargs: ScriptedModel())

    code = main([
        "code",
        "--repo", str(repo),
        "--workspace", "direct",
        "--sandbox", "host",
        "--trust-local",
        "--allow-dirty",
        "--task", "Fix run()",
    ])
    # The run proceeds without raising SystemExit from dirty guard
    assert code in (0, 1)


def test_cli_worktree_mode_succeeds_even_when_source_is_dirty(tmp_path: Path, monkeypatch) -> None:
    from selfforge_agent.runtime.model import ModelTurn

    repo = _init_repo(tmp_path / "cli_worktree_repo")
    (repo / "app.py").write_text("SOURCE_UNCOMMITTED\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    class ScriptedModel:
        def complete(self, messages, tools, timeout):
            return ModelTurn({"role": "assistant", "content": "Done."}, "stop", "test", "test", None)

    monkeypatch.setattr("selfforge_agent.cli.OpenAIChatAdapter", lambda *args, **kwargs: ScriptedModel())

    # Default worktree mode isolates the task; dirty source repo does not block worktree creation
    code = main([
        "code",
        "--repo", str(repo),
        "--workspace", "worktree",
        "--sandbox", "host",
        "--trust-local",
        "--task", "Fix run()",
    ])
    # Succeeded without SystemExit; worktree was created
    assert code in (0, 1)
    # Original source file uncommitted edit is untouched
    assert (repo / "app.py").read_text(encoding="utf-8") == "SOURCE_UNCOMMITTED\n"


def test_web_direct_mode_rejects_dirty_repo_without_allow_dirty(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path / "web_dirty_repo")
    (repo / "app.py").write_text("UNCOMMITTED_WEB\n", encoding="utf-8")

    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "app-workspaces")
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")

    client = TestClient(api_module.app)
    payload = {
        "repository": str(repo),
        "task": "Fix addition",
        "workspace": "direct",
        "confirm_direct": True,
        "sandbox": "host",
        "trust_local": True,
    }
    # Uncommitted repo without allow_dirty must return 409 Conflict
    response = client.post("/api/code/jobs", json=payload)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Direct workspace has uncommitted changes" in detail or "untracked files" in detail
    assert "app.py" in detail
    assert "allow_dirty" in detail


def test_web_direct_mode_allows_dirty_when_allow_dirty_passed(tmp_path: Path, monkeypatch) -> None:
    from selfforge_agent.runtime.model import ModelTurn

    repo = _init_repo(tmp_path / "web_allowed_repo")
    (repo / "app.py").write_text("UNCOMMITTED_WEB\n", encoding="utf-8")

    class ScriptedModel:
        def complete(self, messages, tools, timeout):
            return ModelTurn({"role": "assistant", "content": "Done."}, "stop", "test", "test", None)

    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "app-workspaces")
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: ScriptedModel())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")

    client = TestClient(api_module.app)
    payload = {
        "repository": str(repo),
        "task": "Fix addition",
        "workspace": "direct",
        "confirm_direct": True,
        "allow_dirty": True,
        "sandbox": "host",
        "trust_local": True,
    }
    response = client.post("/api/code/jobs", json=payload)
    assert response.status_code == 200
    assert "job_id" in response.json()
