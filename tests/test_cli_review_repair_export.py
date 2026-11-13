"""Tests for SF-CLI-01: review/repair/generate-test safe output writes and --json export."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfforge_agent.cli import main
from selfforge_agent.coding.worktree import git


def _setup_git_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "Initial commit")
    base_sha = git(repo, "rev-parse", "HEAD").strip()

    (repo / "app.py").write_text("x = 1\neval('2 + 2')\n", encoding="utf-8")
    git(repo, "commit", "-am", "Add dynamic eval")
    head_sha = git(repo, "rev-parse", "HEAD").strip()
    return repo, base_sha, head_sha


def test_cli_review_json_flag_and_safe_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base_sha, head_sha = _setup_git_repo(tmp_path)

    # 1. Default text output (backward compatibility)
    exit_code = main(["review", "--repo", str(repo), "--base", base_sha, "--head", head_sha])
    assert exit_code == 0
    captured_text = capsys.readouterr().out
    assert "Run " in captured_text
    assert "Findings: 1 heuristic suspected" in captured_text

    # 2. --json flag produces pure JSON on stdout
    exit_code = main(["review", "--repo", str(repo), "--base", base_sha, "--head", head_sha, "--json"])
    assert exit_code == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["execution_status"] == "completed"
    assert len(data["findings"]) == 1
    assert data["findings"][0]["file_path"] == "app.py"

    # 3. --output writes validated JSON to file
    out_file = tmp_path / "review_report.json"
    exit_code = main([
        "review", "--repo", str(repo), "--base", base_sha, "--head", head_sha,
        "--output", str(out_file), "--json",
    ])
    assert exit_code == 0
    assert out_file.is_file()
    saved_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved_data["execution_status"] == "completed"


def test_cli_review_rejects_symlinks_directories_and_missing_parents(
    tmp_path: Path,
) -> None:
    repo, base_sha, head_sha = _setup_git_repo(tmp_path)

    # 1. Symlink target rejected
    target_real = tmp_path / "real_file.txt"
    target_real.write_text("pre-existing content", encoding="utf-8")
    symlink_file = tmp_path / "link.json"
    symlink_file.symlink_to(target_real)

    with pytest.raises(SystemExit) as exc:
        main(["review", "--repo", str(repo), "--base", base_sha, "--head", head_sha, "--output", str(symlink_file)])
    assert exc.value.code == 2
    assert target_real.read_text(encoding="utf-8") == "pre-existing content"

    # 2. Directory target rejected
    dir_target = tmp_path / "target_dir"
    dir_target.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["review", "--repo", str(repo), "--base", base_sha, "--head", head_sha, "--output", str(dir_target)])
    assert exc.value.code == 2

    # 3. Missing parent directory rejected
    missing_parent = tmp_path / "no_such_dir" / "report.json"
    with pytest.raises(SystemExit) as exc:
        main(["review", "--repo", str(repo), "--base", base_sha, "--head", head_sha, "--output", str(missing_parent)])
    assert exc.value.code == 2


def test_cli_repair_rejects_symlinks_and_supports_json_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base_sha, head_sha = _setup_git_repo(tmp_path)

    # 1. Symlink target rejected upfront before execution
    target_real = tmp_path / "real_patch.diff"
    target_real.write_text("unmodified", encoding="utf-8")
    symlink_patch = tmp_path / "link_patch.diff"
    symlink_patch.symlink_to(target_real)

    with pytest.raises(SystemExit) as exc:
        main([
            "repair", "--repo", str(repo), "--base", base_sha, "--head", head_sha,
            "--task", "Fix eval", "--patch-output", str(symlink_patch),
        ])
    assert exc.value.code == 2
    assert target_real.read_text(encoding="utf-8") == "unmodified"

    # 2. Mocked repair with --json and safe --output write
    from datetime import datetime

    from selfforge_agent.coding.models import RepairReport
    from selfforge_agent.models import PRContext

    now = datetime.now(UTC)
    mock_report = RepairReport(
        run_id="rep001",
        repository=str(repo),
        base_sha=base_sha,
        head_sha=head_sha,
        task="Fix eval",
        context=PRContext(repository=str(repo), base_sha=base_sha, head_sha=head_sha, title="Test", description="", files=[]),
        findings=[],
        status="verified",
        verified_patch="diff --git a/app.py b/app.py\n",
        attempts=[],
        errors=[],
        started_at=now,
        finished_at=now,
    )

    out_json = tmp_path / "repair.json"
    out_patch = tmp_path / "repair.patch"
    from selfforge_agent.providers import ProviderConfig

    dummy_provider = ProviderConfig("deepseek", "DeepSeek", "key", "model", "https://api.example.com", "")
    with (
        patch("selfforge_agent.cli.RepairAgent") as mock_agent_cls,
        patch("selfforge_agent.cli.route_providers", return_value=[dummy_provider]),
    ):
        instance = MagicMock()
        instance.run.return_value = mock_report
        mock_agent_cls.return_value = instance

        exit_code = main([
            "repair", "--repo", str(repo), "--base", base_sha, "--head", head_sha,
            "--task", "Fix eval", "--output", str(out_json), "--patch-output", str(out_patch),
            "--json",
        ])
        assert exit_code == 0
        captured_json = capsys.readouterr().out
        data = json.loads(captured_json)
        assert data["status"] == "verified"
        assert out_json.is_file()
        assert out_patch.read_text(encoding="utf-8") == "diff --git a/app.py b/app.py\n"


def test_cli_generate_test_rejects_symlinks_and_supports_json_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base_sha, head_sha = _setup_git_repo(tmp_path)

    # 1. Symlink target rejected upfront
    target_real = tmp_path / "real_test.py"
    target_real.write_text("original test", encoding="utf-8")
    symlink_test = tmp_path / "link_test.py"
    symlink_test.symlink_to(target_real)

    with pytest.raises(SystemExit) as exc:
        main([
            "generate-test", "--repo", str(repo), "--base", base_sha, "--head", head_sha,
            "--task", "Detect bug", "--test-output", str(symlink_test),
        ])
    assert exc.value.code == 2
    assert target_real.read_text(encoding="utf-8") == "original test"

    # 2. Mocked generate-test with --json and safe --output write
    from selfforge_agent.coding.test_generation import DifferentialTestReport, GeneratedTest
    mock_result = DifferentialTestReport(
        run_id="gen001",
        repository=str(repo),
        base_sha=base_sha,
        head_sha=head_sha,
        task="Detect bug",
        status="differential",
        generated_test=GeneratedTest(test_code="def test_fail(): assert False\n", rationale="test bug"),
        errors=[],
    )

    out_json = tmp_path / "gen.json"
    out_test = tmp_path / "test_out.py"
    from selfforge_agent.providers import ProviderConfig

    dummy_provider = ProviderConfig("deepseek", "DeepSeek", "key", "model", "https://api.example.com", "")
    with (
        patch("selfforge_agent.cli.DifferentialTestGenerator") as mock_gen_cls,
        patch("selfforge_agent.cli.route_providers", return_value=[dummy_provider]),
    ):
        instance = MagicMock()
        instance.run.return_value = mock_result
        mock_gen_cls.return_value = instance

        exit_code = main([
            "generate-test", "--repo", str(repo), "--base", base_sha, "--head", head_sha,
            "--task", "Detect bug", "--output", str(out_json), "--test-output", str(out_test),
            "--json",
        ])
        assert exit_code == 0
        captured_json = capsys.readouterr().out
        data = json.loads(captured_json)
        assert data["status"] == "differential"
        assert out_json.is_file()
        assert out_test.read_text(encoding="utf-8") == "def test_fail(): assert False\n"
