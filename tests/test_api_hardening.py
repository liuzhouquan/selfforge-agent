"""Security hardening tests for Web Coding API (SF-API-02)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import subprocess
import urllib.parse
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "Initial",
        ],
        check=True,
    )
    return repo


class TestJobIdAndResumeSessionValidation:
    """SF-API-02-A1: Test job_id and resume_session input validation."""

    def test_job_id_validation_rejects_path_traversal_and_malformed(self) -> None:
        client = TestClient(api_module.app)
        invalid_job_ids = [
            "bad..job",
            "job with spaces",
            "invalid!chars",
            "x" * 65,
            "bad..traversal",
            "bad@job",
        ]
        for jid in invalid_job_ids:
            quoted_jid = urllib.parse.quote(jid, safe="")
            res_get = client.get(f"/api/code/jobs/{quoted_jid}")
            assert res_get.status_code == 400, f"Expected 400 for GET job_id={jid!r}, got {res_get.status_code}"
            assert res_get.json()["detail"] == "Invalid job ID"

            res_cancel = client.post(f"/api/code/jobs/{quoted_jid}/cancel")
            assert res_cancel.status_code == 400, f"Expected 400 for POST cancel job_id={jid!r}, got {res_cancel.status_code}"
            assert res_cancel.json()["detail"] == "Invalid job ID"

    def test_start_coding_job_rejects_malformed_resume_session(self, test_repo: Path) -> None:
        client = TestClient(api_module.app)
        invalid_sessions = [
            "bad..session",
            "session with spaces",
            "invalid!chars",
            "x" * 65,
            "../etc/passwd",
        ]
        for sid in invalid_sessions:
            res = client.post(
                "/api/code/jobs",
                json={
                    "repository": str(test_repo),
                    "resume_session": sid,
                    "task": "do something",
                },
            )
            assert res.status_code == 400, f"Expected 400 for resume_session={sid!r}, got {res.status_code}"
            assert res.json()["detail"] == "Invalid session ID"

    def test_provider_models_unconfigured_provider_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = TestClient(api_module.app)
        # Mock configured_providers to return empty list
        monkeypatch.setattr(api_module, "configured_providers", list)
        res = client.get("/api/providers/deepseek/models")
        assert res.status_code == 404
        assert "not configured" in res.json()["detail"]


class TestRepositoryAndWorkspaceInputHardening:
    """SF-API-02-A2: Test repository and workspace input validation and error mapping."""

    def test_workspace_endpoints_reject_nonexistent_or_invalid_repository(self) -> None:
        client = TestClient(api_module.app)
        bad_repo = "/nonexistent/path/for/repo"
        valid_sid = "valid_session_123"

        # remove endpoint
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/remove",
            json={"repository": bad_repo},
        )
        assert res.status_code == 400
        assert "Repository directory does not exist" in res.json()["detail"]

        # revert endpoint
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/revert",
            json={"repository": bad_repo},
        )
        assert res.status_code == 400
        assert "Repository directory does not exist" in res.json()["detail"]

        # commit endpoint
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/commit",
            json={"repository": bad_repo, "message": "feat: test"},
        )
        assert res.status_code == 400
        assert "Repository directory does not exist" in res.json()["detail"]

        # pr endpoint
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/pr",
            json={"repository": bad_repo, "title": "Test PR"},
        )
        assert res.status_code == 400
        assert "Repository directory does not exist" in res.json()["detail"]

    def test_null_bytes_in_paths_return_400_not_500(self) -> None:
        client = TestClient(api_module.app)
        valid_sid = "valid_session_123"

        # sandbox plan with null byte
        res = client.post("/api/sandbox/plan", json={"repository": "/tmp/\x00repo"})
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"

        # sandbox audit with null byte
        res = client.post("/api/sandbox/audit", json={"wheelhouse": "/tmp/\x00wh"})
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"

        # sandbox freshness with null byte
        res = client.post("/api/sandbox/freshness", json={"repository": "/tmp/\x00repo"})
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"

        # validate-regression with null byte
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/validate-regression",
            json={"repository": "/tmp/\x00repo", "test_file": "tests/test_x.py"},
        )
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"

        # remove with null byte
        res = client.post(
            f"/api/code/workspaces/{valid_sid}/remove",
            json={"repository": "/tmp/\x00repo"},
        )
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"

    def test_audit_and_storage_reject_option_injection_and_traversal_head_ref(self, test_repo: Path) -> None:
        client = TestClient(api_module.app)
        bad_refs = ["--upload-pack=evil", "-o", "bad..ref", "refs/heads/../../etc/passwd", "ref\x00bad"]
        for ref in bad_refs:
            res_audit = client.get("/api/workspaces/audit", params={"repository": str(test_repo), "head_ref": ref})
            assert res_audit.status_code == 400, f"Expected 400 for audit head_ref={ref!r}, got {res_audit.status_code}"

            res_storage = client.get("/api/workspaces/storage", params={"repository": str(test_repo), "head_ref": ref})
            assert res_storage.status_code == 400, f"Expected 400 for storage head_ref={ref!r}, got {res_storage.status_code}"


class TestAccessControlAndExceptionHardening:
    """SF-API-02-A3: Test fail-closed require_local and controlled exception mapping."""

    def test_require_local_rejects_missing_client(self) -> None:
        mock_req = MagicMock()
        mock_req.client = None
        with pytest.raises(api_module.HTTPException) as exc_info:
            api_module._require_local(mock_req)
        assert exc_info.value.status_code == 403

    def test_review_endpoint_maps_exceptions_to_400(self, monkeypatch: pytest.MonkeyPatch, test_repo: Path) -> None:
        client = TestClient(api_module.app)
        monkeypatch.setenv("SELFFORGE_REPO_ROOT", str(test_repo))

        # Mock ReviewService.review to raise ValueError
        def mock_review(*args, **kwargs):
            raise ValueError("Invalid revision specification")

        from selfforge_agent.service import ReviewService
        monkeypatch.setattr(ReviewService, "review", mock_review)

        res = client.post("/review", json={"base": "main", "head": "feat"})
        assert res.status_code == 400
        assert "Invalid revision specification" in res.json()["detail"]

    def test_benchmark_diff_format_eval_diff_error_maps_to_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = TestClient(api_module.app)

        def mock_diff_eval_reports(*args, **kwargs):
            return {"summary": {}, "runs": 1}

        def mock_format_eval_diff(*args, **kwargs):
            raise ValueError("Unsupported format requested")

        monkeypatch.setattr(api_module, "diff_eval_reports", mock_diff_eval_reports)
        monkeypatch.setattr(api_module, "format_eval_diff", mock_format_eval_diff)
        res = client.post(
            "/api/benchmark/diff",
            json={
                "before_report": {"summary": {}},
                "after_report": {"summary": {}},
                "format": "markdown",
            },
        )
        assert res.status_code == 400
        assert "Unsupported format requested" in res.json()["detail"]
