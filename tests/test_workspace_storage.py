"""Tests for workspace storage quota and audit (SF-WS-04)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from selfforge_agent.api.app import app
from selfforge_agent.cli import main
from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.workspace import CodingWorkspaceManager, measure_directory


def create_test_repo(path: Path) -> Path:
    repo = path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    return repo


def test_measure_directory_normal_and_caches(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n" * 10, encoding="utf-8")  # ~150 bytes
    (root / "util.py").write_text("x = 1\n", encoding="utf-8")

    cache_dir = root / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "app.cpython-312.pyc").write_bytes(b"\x00" * 300)

    pytest_cache = root / ".pytest_cache" / "v"
    pytest_cache.mkdir(parents=True)
    (pytest_cache / "cache.json").write_bytes(b"\x00" * 200)

    total_b, cache_b, count = measure_directory(root)
    assert count == 4
    assert cache_b == 500  # 300 + 200
    assert total_b > cache_b


def test_measure_directory_symlink_safety(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "code.py").write_text("pass\n", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge.bin").write_bytes(b"\x00" * 10_000)

    # Symlink pointing to outside directory
    (root / "link_outside").symlink_to(outside)
    # Self-referential loop symlink
    (root / "loop").symlink_to(root)

    total_b, _cache_b, count = measure_directory(root)
    # Only code.py and the symlinks themselves (lstat) should be counted, NOT the 10,000 bytes outside!
    assert total_b < 1000
    assert count <= 3


def test_measure_directory_bounded_file_count(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    for i in range(20):
        (root / f"file_{i}.txt").write_text("data\n", encoding="utf-8")

    _total_b, _cache_b, count = measure_directory(root, max_files=5)
    assert count <= 6  # Bounded around max_files


def test_audit_storage_empty_repository(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")

    report = manager.audit_storage(repo)
    assert report["repository"] == str(repo)
    assert report["workspace_count"] == 0
    assert report["total_bytes"] == 0
    assert report["total_cache_bytes"] == 0
    assert report["reclaimable_bytes"] == 0
    assert report["quota_bytes"] is None
    assert report["quota_exceeded"] is False
    assert report["workspaces"] == []


def test_audit_storage_single_worktree_with_caches(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")

    ws = manager.create(repo, "s101")
    (ws.path / "module.py").write_text("x = 42\n", encoding="utf-8")
    cache_dir = ws.path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-312.pyc").write_bytes(b"\x00" * 400)

    report = manager.audit_storage(repo)
    assert report["workspace_count"] == 1
    assert report["total_bytes"] > 400
    assert report["total_cache_bytes"] >= 400
    assert len(report["workspaces"]) == 1

    ws_data = report["workspaces"][0]
    assert ws_data["session_id"] == "s101"
    assert ws_data["cache_bytes"] >= 400
    assert ws_data["total_bytes"] >= ws_data["cache_bytes"]
    assert ws_data["file_count"] >= 2
    assert "recommendations" in report


def test_audit_storage_quota_within_and_exceeded(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")

    ws = manager.create(repo, "s102")
    (ws.path / "big.dat").write_bytes(b"\x00" * 5000)

    # Within generous quota
    report_within = manager.audit_storage(repo, quota_bytes=10_000_000)
    assert report_within["quota_bytes"] == 10_000_000
    assert report_within["quota_exceeded"] is False

    # Exceeding small quota
    report_over = manager.audit_storage(repo, quota_bytes=100)
    assert report_over["quota_bytes"] == 100
    assert report_over["quota_exceeded"] is True
    assert any("exceeds quota" in r.lower() for r in report_over["recommendations"])


def test_audit_storage_reclaimable_calculation(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")

    # ws1: merged and clean -> prunable -> total_bytes reclaimable
    ws1 = manager.create(repo, "s103")
    (ws1.path / "file1.txt").write_text("clean merged\n", encoding="utf-8")
    git(ws1.path, "add", "file1.txt")
    git(ws1.path, "commit", "-m", "Commit in ws1")
    git(repo, "merge", "selfforge/s103")

    # ws2: unmerged with uncommitted modification -> dirty -> only cache_bytes reclaimable
    ws2 = manager.create(repo, "s104")
    (ws2.path / "README.md").write_text("dirty uncommitted\n", encoding="utf-8")
    cache_dir = ws2.path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "cache.pyc").write_bytes(b"\x00" * 250)

    report = manager.audit_storage(repo)
    ws_map = {w["session_id"]: w for w in report["workspaces"]}

    assert ws_map["s103"]["prunable"] is True
    assert ws_map["s103"]["reclaimable_bytes"] == ws_map["s103"]["total_bytes"]

    assert ws_map["s104"]["prunable"] is False
    assert ws_map["s104"]["is_dirty"] is True
    assert ws_map["s104"]["reclaimable_bytes"] == ws_map["s104"]["cache_bytes"]

    assert report["reclaimable_bytes"] == (
        ws_map["s103"]["total_bytes"] + ws_map["s104"]["cache_bytes"]
    )


def test_cli_workspaces_storage_json_and_exit_codes(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(tmp_path / "worktrees"))
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")
    ws = manager.create(repo, "s105")
    (ws.path / "test.txt").write_bytes(b"\x00" * 1000)

    # CLI storage audit with JSON output
    code = main(["workspaces", "storage", "--repo", str(repo), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["workspace_count"] == 1
    assert data["total_bytes"] >= 1000
    assert data["quota_exceeded"] is False

    # CLI storage audit exceeding quota -> exit code 1
    code_over = main([
        "workspaces", "storage", "--repo", str(repo), "--quota-bytes", "50", "--json"
    ])
    assert code_over == 1
    captured_over = capsys.readouterr()
    data_over = json.loads(captured_over.out)
    assert data_over["quota_exceeded"] is True


def test_api_workspaces_storage_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(tmp_path / "worktrees"))
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")
    manager.create(repo, "s106")

    client = TestClient(app)
    response = client.get(f"/api/workspaces/storage?repository={repo}&quota_bytes=50000")
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == str(repo)
    assert data["workspace_count"] == 1
    assert data["quota_bytes"] == 50000
    assert data["quota_exceeded"] is False
    assert len(data["workspaces"]) == 1
    assert data["workspaces"][0]["session_id"] == "s106"


def test_cli_workspaces_storage_output_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(tmp_path / "worktrees"))
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")
    manager.create(repo, "s107")

    out_file = tmp_path / "report.json"
    code = main(["workspaces", "storage", "--repo", str(repo), "--output", str(out_file)])
    assert code == 0
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["workspace_count"] == 1
    assert data["workspaces"][0]["session_id"] == "s107"


def test_cli_workspaces_storage_human_readable_output(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(tmp_path / "worktrees"))
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path / "worktrees")
    manager.create(repo, "s108")

    code = main(["workspaces", "storage", "--repo", str(repo)])
    assert code == 0
    captured = capsys.readouterr()
    assert "Workspace Storage Audit" in captured.out
    assert "Active Workspaces: 1" in captured.out
    assert "s108" in captured.out
