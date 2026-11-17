"""Tests for SF-CLI-02: code and test subcommands safe output writes, --json export, and parameter defense."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfforge_agent.cli import main
from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.evaluation import CASES, OfflineFixtureModel
from selfforge_agent.verification.runner import TestResult as VerificationTestResult


def _setup_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    (repo / "app.py").write_text(CASES[0].source, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(CASES[0].test, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial fixture commit")
    return repo


class TestCliCodeExportAndDefense:
    """SF-CLI-02-A1: code subcommand --json, --output safe write, and parameter validation."""

    def test_cli_code_rejects_invalid_max_steps(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--max-steps", "0"])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "max-steps" in captured.lower()

    def test_cli_code_rejects_invalid_time_budget(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--time-budget", "0"])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "time-budget" in captured.lower()

    def test_cli_code_rejects_invalid_context_tokens(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--context-tokens", "10"])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "context-tokens" in captured.lower()

    def test_cli_code_rejects_symlink_output(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        target = tmp_path / "real_code.json"
        symlink = tmp_path / "sym_code.json"
        symlink.symlink_to(target)

        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--output", str(symlink)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "symlink" in captured.lower()

    def test_cli_code_rejects_missing_parent_output(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        nonexistent = tmp_path / "nonexistent" / "output.json"
        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--output", str(nonexistent)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "directory does not exist" in captured.lower()

    def test_cli_code_json_and_output_exports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        out_file = tmp_path / "code_report.json"
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        from selfforge_agent import cli

        monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: OfflineFixtureModel(CASES[0]))

        exit_code = main([
            "code",
            "--repo", str(repo),
            "--task", "Fix app.py so add(2, 3) returns 5",
            "--sandbox", "host",
            "--trust-local",
            "--model", "fixture",
            "--base-url", "https://example.com/v1",
            "--output", str(out_file),
            "--json",
        ])
        assert exit_code == 0
        captured_json = capsys.readouterr().out
        data = json.loads(captured_json)
        assert data["status"] == "completed"
        assert data["steps"] > 0
        assert "session_id" in data
        assert out_file.exists()
        file_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert file_data["session_id"] == data["session_id"]
        assert file_data["status"] == "completed"


class TestCliTestExportAndDefense:
    """SF-CLI-02-A2: test subcommand --json, --output safe write, and parameter validation."""

    def test_cli_test_rejects_invalid_timeout(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["test", "--repo", str(repo), "--timeout", "0"])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "timeout" in captured.lower()

    def test_cli_test_rejects_nonexistent_repo(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        nonexistent = tmp_path / "no_such_repo"
        with pytest.raises(SystemExit) as exc:
            main(["test", "--repo", str(nonexistent)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "repository" in captured.lower()

    def test_cli_test_rejects_symlink_output(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        target = tmp_path / "real_test.json"
        symlink = tmp_path / "sym_test.json"
        symlink.symlink_to(target)

        with pytest.raises(SystemExit) as exc:
            main(["test", "--repo", str(repo), "--output", str(symlink)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "symlink" in captured.lower()

    def test_cli_test_host_json_and_output_exports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        out_file = tmp_path / "test_report.json"

        from selfforge_agent.verification.runner import TestRunner

        fake_report = VerificationTestResult(
            command=["pytest", "tests"],
            environment="local",
            exit_code=0,
            duration_seconds=0.5,
            status="passed",
            stdout="1 passed",
            stderr="",
            error_message=None,
            failure_details=[],
            passed=1,
            failed=0,
            errors=0,
            skipped=0,
        )
        monkeypatch.setattr(TestRunner, "run", lambda *args, **kwargs: fake_report)

        exit_code = main([
            "test",
            "--repo", str(repo),
            "--sandbox", "host",
            "--trust-local",
            "--output", str(out_file),
            "--json",
        ])
        assert exit_code == 0
        captured_json = capsys.readouterr().out
        data = json.loads(captured_json)
        assert data["sandbox"] == "host"
        assert data["status"] == "passed"
        assert data["passed"] == 1
        assert out_file.exists()
        file_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert file_data["status"] == "passed"
        assert file_data["passed"] == 1

    def test_cli_test_docker_json_and_output_exports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        out_file = tmp_path / "docker_test_report.json"

        from selfforge_agent.runtime.tools import ToolOutcome, ToolRegistry

        monkeypatch.setattr(
            ToolRegistry,
            "execute",
            lambda *args, **kwargs: ToolOutcome("passed=1 failed=0", True),
        )

        exit_code = main([
            "test",
            "--repo", str(repo),
            "--sandbox", "docker",
            "--output", str(out_file),
            "--json",
        ])
        assert exit_code == 0
        captured_json = capsys.readouterr().out
        data = json.loads(captured_json)
        assert data["sandbox"] == "docker"
        assert data["ok"] is True
        assert out_file.exists()
        file_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert file_data["ok"] is True


class TestCliSharedExportCollisions:
    """SF-CLI-02-A3: shared collision prevention and non-regular file write defense."""

    def test_cli_code_rejects_output_collision_with_repo_dir(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["code", "--repo", str(repo), "--task", "Fix add", "--output", str(repo)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "regular file" in captured.lower() or "collide" in captured.lower()

    def test_cli_test_rejects_output_collision_with_repo_dir(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _setup_git_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["test", "--repo", str(repo), "--output", str(repo)])
        assert exc.value.code == 2
        captured = capsys.readouterr().err
        assert "regular file" in captured.lower() or "collide" in captured.lower()
