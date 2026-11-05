"""Tests for workspace audit and safe prune functionality (SF-WS-02)."""

import json
import subprocess
from pathlib import Path

from selfforge_agent.runtime.workspace import (
    CodingWorkspaceManager,
    try_workspace_lock,
    unlock_workspace,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-b", "main"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=t@e.com", "commit", "-qm", "Initial"],
        check=True,
    )
    return path


def test_workspace_audit_and_prune(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")

    # 1. Merged and clean worktree
    ws_merged = manager.create(repo, "sessmerged")
    (ws_merged.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(ws_merged.path), "-c", "user.name=Test", "-c", "user.email=t@e.com", "commit", "-am", "Fix 2"],
        check=True,
    )
    # Merge into main
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@e.com", "merge", "--no-ff", ws_merged.branch, "-m", "Merge fix"],
        check=True,
    )

    # 2. Dirty worktree
    ws_dirty = manager.create(repo, "sessdirty")
    (ws_dirty.path / "app.py").write_text("DIRTY = True\n", encoding="utf-8")

    # 3. Unmerged clean worktree
    ws_unmerged = manager.create(repo, "sessunmerged")
    (ws_unmerged.path / "app.py").write_text("UNMERGED = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(ws_unmerged.path), "-c", "user.name=Test", "-c", "user.email=t@e.com", "commit", "-am", "Unmerged feature"],
        check=True,
    )

    # Audit check
    audit_list = manager.audit(repo)
    by_id = {item["session_id"]: item for item in audit_list}

    assert len(by_id) == 3
    assert by_id["sessmerged"]["is_dirty"] is False
    assert by_id["sessmerged"]["is_merged"] is True
    assert by_id["sessmerged"]["is_locked"] is False
    assert by_id["sessmerged"]["prunable"] is True

    assert by_id["sessdirty"]["is_dirty"] is True
    assert by_id["sessdirty"]["prunable"] is False

    assert by_id["sessunmerged"]["is_dirty"] is False
    assert by_id["sessunmerged"]["is_merged"] is False
    assert by_id["sessunmerged"]["prunable"] is False

    # Dry-run prune
    dry_res = manager.prune(repo, dry_run=True)
    assert dry_res["pruned"] == ["sessmerged"]
    assert "sessdirty" in dry_res["skipped_dirty"]
    assert "sessunmerged" in dry_res["skipped_unmerged"]
    assert ws_merged.path.is_dir()

    # Actual prune
    res = manager.prune(repo, dry_run=False)
    assert res["pruned"] == ["sessmerged"]
    assert not ws_merged.path.exists()
    assert ws_dirty.path.is_dir()
    assert ws_unmerged.path.is_dir()

    # Prune with include_unmerged
    unmerged_res = manager.prune(repo, merged_only=False)
    assert unmerged_res["pruned"] == ["sessunmerged"]
    assert not ws_unmerged.path.exists()
    # Dirty must STILL be skipped!
    assert "sessdirty" in unmerged_res["skipped_dirty"]
    assert ws_dirty.path.is_dir()


def test_workspace_prune_skips_locked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")

    ws = manager.create(repo, "sesslocked")
    # Merge it so it would otherwise be prunable
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@e.com", "merge", "--no-ff", ws.branch, "-m", "Merge"],
        check=True,
    )

    # Acquire lock
    fd = try_workspace_lock(ws.path, root=manager.locks_root)
    assert fd is not None
    try:
        res = manager.prune(repo)
        assert res["pruned"] == []
        assert "sesslocked" in res["skipped_locked"]
        assert ws.path.is_dir()
    finally:
        unlock_workspace(fd)

    # Once unlocked, prune succeeds
    res_unlocked = manager.prune(repo)
    assert res_unlocked["pruned"] == ["sesslocked"]
    assert not ws.path.exists()


def test_cli_workspaces_audit_and_prune(tmp_path: Path, monkeypatch, capsys) -> None:
    from selfforge_agent.cli import main

    repo = _init_repo(tmp_path / "repo")
    root = tmp_path / "custom_workspaces"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(root))

    manager = CodingWorkspaceManager(root)
    ws = manager.create(repo, "sesscli")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@e.com", "merge", "--no-ff", ws.branch, "-m", "Merge"],
        check=True,
    )

    # Audit CLI JSON
    code = main(["workspaces", "audit", "--repo", str(repo), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    audit_data = json.loads(captured.out)
    assert len(audit_data) == 1
    assert audit_data[0]["session_id"] == "sesscli"
    assert audit_data[0]["prunable"] is True

    # Prune CLI dry-run
    code = main(["workspaces", "prune", "--repo", str(repo), "--dry-run"])
    assert code == 0
    assert ws.path.is_dir()

    # Prune CLI actual
    code = main(["workspaces", "prune", "--repo", str(repo)])
    assert code == 0
    assert not ws.path.exists()


def test_api_workspaces_audit_and_prune_endpoints(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app

    client = TestClient(app)
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager()
    ws = manager.create(repo, "sessapi")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@e.com", "merge", "--no-ff", ws.branch, "-m", "Merge"],
        check=True,
    )

    # Audit endpoint
    res = client.get(f"/api/workspaces/audit?repository={repo}")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    item = next(w for w in data if w["session_id"] == "sessapi")
    assert item["prunable"] is True

    # Prune endpoint
    res = client.post("/api/workspaces/prune", json={
        "repository": str(repo),
        "dry_run": False,
    })
    assert res.status_code == 200
    prune_res = res.json()
    assert "sessapi" in prune_res["pruned"]
    assert not ws.path.exists()


def test_workspace_audit_and_prune_empty_and_text_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    from selfforge_agent.cli import main

    repo = _init_repo(tmp_path / "repo")
    root = tmp_path / "empty_workspaces"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(root))

    manager = CodingWorkspaceManager(root)
    assert manager.audit(repo) == []
    empty_prune = manager.prune(repo)
    assert empty_prune == {
        "pruned": [],
        "skipped_locked": [],
        "skipped_dirty": [],
        "skipped_unmerged": [],
    }

    # Text mode CLI audit
    code = main(["workspaces", "audit", "--repo", str(repo)])
    assert code == 0
    captured = capsys.readouterr()
    assert "No worktrees found." in captured.out

    # Text mode CLI prune
    code = main(["workspaces", "prune", "--repo", str(repo)])
    assert code == 0
    captured = capsys.readouterr()
    assert "No workspaces pruned." in captured.out


def test_workspace_prune_cli_session_id_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    import pytest

    from selfforge_agent.cli import main

    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(SystemExit):
        main(["workspaces", "audit", "extra_session_id", "--repo", str(repo)])

    with pytest.raises(SystemExit):
        main(["workspaces", "prune", "extra_session_id", "--repo", str(repo)])

