"""Security and robustness tests for Web Coding API and session management (SF-API-01)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import os
import subprocess
import urllib.parse
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.api.coding_jobs import CodingJobManager, DurableCancel
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Initial"],
        check=True,
    )
    return repo


def test_payload_limit_rejects_oversized_requests() -> None:
    client = TestClient(api_module.app)
    # Content-Length > 2MB
    headers = {"Content-Length": str(3 * 1024 * 1024)}
    response = client.post("/api/benchmark/diff", content=b"{}", headers=headers)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_payload_limit_rejects_malformed_content_length() -> None:
    client = TestClient(api_module.app)
    # Negative Content-Length
    headers = {"Content-Length": "-10"}
    response = client.post("/api/benchmark/diff", content=b"{}", headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}

    # Non-integer Content-Length
    headers = {"Content-Length": "invalid"}
    response = client.post("/api/benchmark/diff", content=b"{}", headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length header"}


def test_session_id_validation_rejects_path_traversal_and_malformed(test_repo: Path) -> None:
    client = TestClient(api_module.app)
    invalid_session_ids = [
        "bad..session",
        "session with spaces",
        "invalid!chars",
        "bad@session",
        "x" * 65,  # exceeds 64 chars
    ]

    for sid in invalid_session_ids:
        if not sid:
            continue
        quoted_sid = urllib.parse.quote(sid, safe="")
        # Test remove
        res = client.post(
            f"/api/code/workspaces/{quoted_sid}/remove",
            json={"repository": str(test_repo)},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test revert
        res = client.post(
            f"/api/code/workspaces/{quoted_sid}/revert",
            json={"repository": str(test_repo)},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test diff
        res = client.get(
            f"/api/code/workspaces/{quoted_sid}/diff",
            params={"repository": str(test_repo)},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test commit
        res = client.post(
            f"/api/code/workspaces/{quoted_sid}/commit",
            json={"repository": str(test_repo), "message": "feat: test"},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test pr
        res = client.post(
            f"/api/code/workspaces/{quoted_sid}/pr",
            json={"repository": str(test_repo), "title": "Test PR"},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test validate-regression
        res = client.post(
            f"/api/code/workspaces/{quoted_sid}/validate-regression",
            json={"repository": str(test_repo), "test_file": "tests/test_x.py"},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}

        # Test get regression
        res = client.get(
            f"/api/code/workspaces/{quoted_sid}/regression",
            params={"repository": str(test_repo)},
        )
        assert res.status_code == 400
        assert res.json() == {"detail": "Invalid session ID"}


def test_durable_cancel_rejects_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("precious data", encoding="utf-8")
    marker = tmp_path / "cancel_marker"
    marker.symlink_to(victim)

    cancel = DurableCancel(marker, Event())
    # is_set should not consider a symlink as set
    assert not cancel.is_set()

    # set() should refuse to open/truncate symlink target
    with pytest.raises(ValueError, match="symlink"):
        cancel.set()

    assert victim.read_text(encoding="utf-8") == "precious data"


def test_load_record_rejects_symlinks_fifos_and_oversized_files(tmp_path: Path) -> None:
    mgr = CodingJobManager(root=tmp_path / "jobs")
    job_id = "0123456789ab"
    record_file = mgr.records / f"{job_id}.json"

    # 1. Symlink
    victim = tmp_path / "victim.json"
    victim.write_text(json.dumps({"job_id": job_id, "workspace_path": "/tmp", "session_root": "/tmp", "session_id": "abc"}), encoding="utf-8")
    record_file.symlink_to(victim)

    with pytest.raises(KeyError):
        mgr._load_record(job_id)

    record_file.unlink()

    # 2. FIFO
    os.mkfifo(record_file)
    with pytest.raises(KeyError):
        mgr._load_record(job_id)

    record_file.unlink()

    # 3. Oversized file (> 64KB)
    record_file.write_text(" " * 65537, encoding="utf-8")
    with pytest.raises(KeyError):
        mgr._load_record(job_id)


def test_write_record_rejects_symlink_target(tmp_path: Path) -> None:
    mgr = CodingJobManager(root=tmp_path / "jobs")
    job_id = "0123456789ab"
    record_file = mgr.records / f"{job_id}.json"

    victim = tmp_path / "victim.json"
    victim.write_text("initial", encoding="utf-8")
    record_file.symlink_to(victim)

    record = {
        "job_id": job_id,
        "session_id": "s1",
        "session_root": str(tmp_path),
        "state": "running",
        "answer": "",
        "steps": 0,
        "tools_used": 0,
        "error": "",
        "repository": str(tmp_path),
        "workspace_path": str(tmp_path),
        "branch": "",
        "base_sha": "",
        "updated_at": 1000.0,
    }

    with pytest.raises(ValueError, match="symlink"):
        mgr._write_record(record)

    assert victim.read_text(encoding="utf-8") == "initial"


def test_list_jobs_tolerates_broken_symlinks_and_corrupt_files(tmp_path: Path) -> None:
    mgr = CodingJobManager(root=tmp_path / "jobs")
    # Create a broken symlink in records
    broken_link = mgr.records / "broken.json"
    broken_link.symlink_to(tmp_path / "does_not_exist")

    # Create a corrupt json file
    corrupt = mgr.records / "corrupt.json"
    corrupt.write_text("{bad json", encoding="utf-8")

    # list_jobs should safely ignore both without raising
    jobs = mgr.list_jobs()
    assert isinstance(jobs, list)


def test_coding_job_manager_rejects_invalid_limits(tmp_path: Path) -> None:
    for invalid in (0, -1, float("nan"), float("inf"), True, False, 1.5):
        with pytest.raises(ValueError, match="Positive job limits are required"):
            CodingJobManager(max_active=invalid, root=tmp_path / "jobs")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Positive job limits are required"):
            CodingJobManager(max_history=invalid, root=tmp_path / "jobs")  # type: ignore[arg-type]


def test_corrupted_regression_record_returns_422(monkeypatch: pytest.MonkeyPatch, test_repo: Path, tmp_path: Path) -> None:
    # Setup session store with corrupted regression record
    session_id = "abc12345"
    sessions_root = tmp_path / "sessions"
    mgr = CodingWorkspaceManager(root=tmp_path / "worktrees")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", mgr)

    # Mock session_root
    def mock_session_root(repo: Path, sid: str | None = None, **kwargs) -> Path:
        return sessions_root

    monkeypatch.setattr(mgr, "session_root", mock_session_root)
    monkeypatch.setattr(mgr, "validate_repository", lambda r, **kwargs: test_repo)

    rec_dir = sessions_root / ".selfforge" / "sessions"
    rec_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(api_module.SessionStore, "latest_regression_record", lambda self: {"corrupt": "data"})

    client = TestClient(api_module.app)
    response = client.get(
        f"/api/code/workspaces/{session_id}/regression",
        params={"repository": str(test_repo)},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Corrupted regression validation record"}


def test_storage_audit_rejects_negative_quota(test_repo: Path) -> None:
    client = TestClient(api_module.app)
    response = client.get(
        "/api/workspaces/storage",
        params={"repository": str(test_repo), "quota_bytes": -1},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "quota_bytes must be non-negative"}


def test_repository_endpoints_reject_invalid_or_nonexistent_repo() -> None:
    client = TestClient(api_module.app)
    bad_repo = "/nonexistent/path/for/repo"

    res = client.get("/api/code/environment", params={"repository": bad_repo})
    assert res.status_code == 400
    assert "Repository directory does not exist" in res.json()["detail"]

    res = client.get("/api/workspaces/audit", params={"repository": bad_repo})
    assert res.status_code == 400
    assert "Repository directory does not exist" in res.json()["detail"]

    res = client.get("/api/workspaces/storage", params={"repository": bad_repo})
    assert res.status_code == 400
    assert "Repository directory does not exist" in res.json()["detail"]

    res = client.post("/api/workspaces/prune", json={"repository": bad_repo})
    assert res.status_code == 400
    assert "Repository directory does not exist" in res.json()["detail"]

    res = client.get("/api/code/jobs", params={"repository": bad_repo})
    assert res.status_code == 400
    assert "Repository directory does not exist" in res.json()["detail"]


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_actual_request_bytes_are_limited_even_without_honest_length(declared_length: str | None) -> None:
    body = b'{"task":"' + b"x" * (2 * 1024 * 1024) + b'"}'
    headers = {"content-type": "application/json"}
    if declared_length is not None:
        headers["content-length"] = declared_length
    response = TestClient(api_module.app).post("/api/code/jobs", content=iter([body]), headers=headers)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_fifo_cancel_marker_is_rejected_without_blocking(tmp_path: Path) -> None:
    marker = tmp_path / "cancel"
    os.mkfifo(marker)
    code = (
        "from pathlib import Path\nfrom threading import Event\nimport sys\n"
        "from selfforge_agent.api.coding_jobs import DurableCancel\n"
        "try:\n DurableCancel(Path(sys.argv[1]), Event()).set()\n"
        "except ValueError:\n sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(marker)], timeout=3, check=False)
    assert result.returncode == 0


def test_list_jobs_skips_record_with_non_string_state(tmp_path: Path) -> None:
    manager = CodingJobManager(root=tmp_path / "jobs")
    job_id = "0123456789ab"
    record = {"job_id": job_id, "session_id": "s1", "session_root": str(tmp_path),
              "workspace_path": str(tmp_path), "state": []}
    (manager.records / f"{job_id}.json").write_text(json.dumps(record))
    assert manager.list_jobs() == []


def test_fake_git_marker_does_not_establish_repository(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    response = TestClient(api_module.app).get("/api/code/environment", params={"repository": str(tmp_path)})
    assert response.status_code == 400


def test_job_writer_does_not_publish_unreadably_large_record(tmp_path: Path) -> None:
    manager = CodingJobManager(root=tmp_path / "jobs")
    job_id = "0123456789ab"
    with pytest.raises(ValueError, match="64 KiB"):
        manager._write_record({"job_id": job_id, "answer": "x" * 65536})
    assert not (manager.records / f"{job_id}.json").exists()
