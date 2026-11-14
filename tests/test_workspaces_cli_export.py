"""Tests for SF-WS-07: workspaces CLI --json and safe --output export alignment."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.cli import main
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Tester"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tester@example.com"], check=True)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "Initial commit"], check=True)
    return repo


def test_workspaces_list_and_diff_json_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    ws_root = tmp_path / "worktrees"
    manager = CodingWorkspaceManager(ws_root)
    ws = manager.create(repo, "sess01")
    (ws.path / "app.py").write_text("x = 2\n", encoding="utf-8")

    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(ws_root))

    # 1. workspaces list default text vs --json
    exit_code = main(["workspaces", "list", "--repo", str(repo)])
    assert exit_code == 0
    text_out = capsys.readouterr().out
    assert "sess01" in text_out

    exit_code = main(["workspaces", "list", "--repo", str(repo), "--json"])
    assert exit_code == 0
    json_out = capsys.readouterr().out
    list_data = json.loads(json_out)
    assert isinstance(list_data, list)
    assert len(list_data) == 1
    assert list_data[0]["session_id"] == "sess01"
    assert list_data[0]["branch"] == ws.branch

    # list with --output
    list_file = tmp_path / "list.json"
    exit_code = main(["workspaces", "list", "--repo", str(repo), "--output", str(list_file), "--json"])
    assert exit_code == 0
    assert list_file.is_file()
    saved_list = json.loads(list_file.read_text(encoding="utf-8"))
    assert saved_list[0]["session_id"] == "sess01"

    # 2. workspaces diff default text vs --json vs --output
    exit_code = main(["workspaces", "diff", "sess01", "--repo", str(repo)])
    assert exit_code == 0
    diff_text = capsys.readouterr().out
    assert "app.py" in diff_text

    exit_code = main(["workspaces", "diff", "sess01", "--repo", str(repo), "--json"])
    assert exit_code == 0
    diff_json = capsys.readouterr().out
    diff_data = json.loads(diff_json)
    assert "files" in diff_data
    assert len(diff_data["files"]) == 1
    assert diff_data["files"][0]["path"] == "app.py"

    diff_file = tmp_path / "diff.json"
    exit_code = main(["workspaces", "diff", "sess01", "--repo", str(repo), "--output", str(diff_file), "--json"])
    assert exit_code == 0
    assert diff_file.is_file()
    saved_diff = json.loads(diff_file.read_text(encoding="utf-8"))
    assert saved_diff["files"][0]["path"] == "app.py"


def test_workspaces_commit_and_revert_json_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    ws_root = tmp_path / "worktrees"
    manager = CodingWorkspaceManager(ws_root)
    ws = manager.create(repo, "sess02")
    (ws.path / "app.py").write_text("x = 42\n", encoding="utf-8")

    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(ws_root))

    # 1. workspaces commit --json and --output
    commit_file = tmp_path / "commit.json"
    exit_code = main([
        "workspaces", "commit", "sess02", "--repo", str(repo),
        "-m", "feat: test commit", "--output", str(commit_file), "--json",
    ])
    assert exit_code == 0
    commit_json = capsys.readouterr().out
    commit_data = json.loads(commit_json)
    assert commit_data["session_id"] == "sess02"
    assert commit_data["message"] == "feat: test commit"
    assert commit_data["files_committed"] == ["app.py"]
    assert commit_file.is_file()
    saved_commit = json.loads(commit_file.read_text(encoding="utf-8"))
    assert saved_commit["commit_sha"] == commit_data["commit_sha"]

    # 2. Modify and workspaces revert --json and --output
    (ws.path / "app.py").write_text("x = 999\n", encoding="utf-8")
    revert_file = tmp_path / "revert.json"
    exit_code = main([
        "workspaces", "revert", "sess02", "--repo", str(repo),
        "--output", str(revert_file), "--json",
    ])
    assert exit_code == 0
    revert_json = capsys.readouterr().out
    revert_data = json.loads(revert_json)
    assert revert_data["session_id"] == "sess02"
    assert "app.py" in revert_data["reverted_files"]
    assert revert_file.is_file()


def test_workspaces_pr_and_remove_json_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare_remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main", "-q"], check=True)

    ws_root = tmp_path / "worktrees"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(ws_root))

    manager = CodingWorkspaceManager(ws_root)
    ws = manager.create(repo, "sess03")
    (ws.path / "app.py").write_text("x = 100\n", encoding="utf-8")
    main(["workspaces", "commit", "sess03", "--repo", str(repo), "-m", "add 100"])
    capsys.readouterr()

    # 1. workspaces pr --json and --output
    pr_file = tmp_path / "pr.json"
    with patch("selfforge_agent.runtime.delivery._run_gh") as mock_gh:
        mock_gh.side_effect = ["[]", "https://github.com/owner/repo/pull/101\n"]
        exit_code = main([
            "workspaces", "pr", "sess03", "--repo", str(repo),
            "--title", "Add 100", "--output", str(pr_file), "--json",
        ])
        assert exit_code == 0
        pr_json = capsys.readouterr().out
        pr_data = json.loads(pr_json)
        assert pr_data["pr_url"] == "https://github.com/owner/repo/pull/101"
        assert pr_data["pr_number"] == 101
        assert pr_file.is_file()

    # 2. workspaces remove --json and --output
    remove_file = tmp_path / "remove.json"
    exit_code = main([
        "workspaces", "remove", "sess03", "--repo", str(repo),
        "--output", str(remove_file), "--json",
    ])
    assert exit_code == 0
    remove_json = capsys.readouterr().out
    remove_data = json.loads(remove_json)
    assert remove_data["session_id"] == "sess03"
    assert remove_data["removed"] is True
    assert remove_file.is_file()


def test_workspaces_audit_prune_and_storage_json_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    ws_root = tmp_path / "worktrees"
    manager = CodingWorkspaceManager(ws_root)
    manager.create(repo, "sess04")

    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(ws_root))

    # 1. workspaces audit --json and --output
    audit_file = tmp_path / "audit.json"
    exit_code = main(["workspaces", "audit", "--repo", str(repo), "--output", str(audit_file), "--json"])
    assert exit_code == 0
    audit_json = capsys.readouterr().out
    audit_data = json.loads(audit_json)
    assert isinstance(audit_data, list)
    assert audit_file.is_file()
    assert json.loads(audit_file.read_text(encoding="utf-8")) == audit_data

    # 2. workspaces prune --json and --output
    prune_file = tmp_path / "prune.json"
    exit_code = main(["workspaces", "prune", "--repo", str(repo), "--dry-run", "--output", str(prune_file), "--json"])
    assert exit_code == 0
    prune_json = capsys.readouterr().out
    prune_data = json.loads(prune_json)
    assert "pruned" in prune_data
    assert prune_file.is_file()

    # 3. workspaces storage --json --output must NOT contaminate stdout with text
    storage_file = tmp_path / "storage.json"
    exit_code = main(["workspaces", "storage", "--repo", str(repo), "--output", str(storage_file), "--json"])
    assert exit_code == 0
    storage_json = capsys.readouterr().out
    # If stdout was contaminated with "Workspace storage audit report written to...", json.loads will fail!
    storage_data = json.loads(storage_json)
    assert "workspace_count" in storage_data
    assert storage_file.is_file()

    # 4. workspaces storage --output without --json prints notice and writes file
    storage_file2 = tmp_path / "storage2.json"
    exit_code = main(["workspaces", "storage", "--repo", str(repo), "--output", str(storage_file2)])
    assert exit_code == 0
    storage_text = capsys.readouterr().out
    assert "Workspace storage audit report written to" in storage_text
    assert storage_file2.is_file()


def test_workspaces_rejects_symlinks_directories_and_missing_parents(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)

    # 1. Symlink target rejected upfront
    target_real = tmp_path / "real.json"
    target_real.write_text("pre-existing content", encoding="utf-8")
    symlink_file = tmp_path / "link.json"
    symlink_file.symlink_to(target_real)

    with pytest.raises(SystemExit) as exc:
        main(["workspaces", "list", "--repo", str(repo), "--output", str(symlink_file)])
    assert exc.value.code == 2
    assert target_real.read_text(encoding="utf-8") == "pre-existing content"

    # 2. Directory target rejected
    dir_target = tmp_path / "target_dir"
    dir_target.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["workspaces", "list", "--repo", str(repo), "--output", str(dir_target)])
    assert exc.value.code == 2

    # 3. Missing parent rejected
    missing_parent = tmp_path / "missing_dir" / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["workspaces", "list", "--repo", str(repo), "--output", str(missing_parent)])
    assert exc.value.code == 2


@pytest.mark.parametrize("action", ["remove", "revert"])
def test_workspace_mutation_rejects_report_inside_affected_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    repo = _init_repo(tmp_path)
    root = tmp_path / "worktrees"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(root))
    ws = CodingWorkspaceManager(root).create(repo, "sessreport")
    with pytest.raises(SystemExit) as error:
        main(["workspaces", action, "sessreport", "--repo", str(repo),
              "--output", str(ws.path / "report.json"), "--json"])
    assert error.value.code == 2
    assert ws.path.is_dir()
    assert not (ws.path / "report.json").exists()


def test_commit_report_hardlink_is_rejected_before_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    root = tmp_path / "worktrees"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(root))
    ws = CodingWorkspaceManager(root).create(repo, "hardreport")
    source = ws.path / "app.py"
    source.write_text("x = 42\n")
    report = tmp_path / "outside.json"
    report.hardlink_to(source)
    before = subprocess.check_output(["git", "-C", str(ws.path), "rev-parse", "HEAD"])
    with pytest.raises(SystemExit) as error:
        main(["workspaces", "commit", "hardreport", "--repo", str(repo),
              "-m", "keep source", "--output", str(report), "--json"])
    assert error.value.code == 2
    assert subprocess.check_output(["git", "-C", str(ws.path), "rev-parse", "HEAD"]) == before
    assert source.read_text() == "x = 42\n"
