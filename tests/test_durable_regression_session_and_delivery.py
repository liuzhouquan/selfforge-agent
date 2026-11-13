"""Tests for SF-TEST-03: SessionStore regression records, GET endpoint, and PR delivery binding."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import create_pull_request
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace


def test_session_store_regression_records_and_snapshot(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    session = SessionStore(session_root, "testsession01")

    # 1. Initially empty
    assert session.regression_records() == []
    assert session.latest_regression_record() is None
    assert session.confirmed_regression_snapshot() is None

    # 2. Append inconclusive record
    reg_path = session.path.with_name(f"{session.session_id}.regression.jsonl")
    SessionStore._append(
        reg_path,
        {
            "status": "inconclusive",
            "reason": "both_passed",
            "session_id": session.session_id,
            "base_sha": "0" * 40,
            "workspace_sha256": "a" * 64,
            "test_sha256": "t1" * 32,
        },
    )

    records = session.regression_records()
    assert len(records) == 1
    assert records[0]["status"] == "inconclusive"
    assert session.latest_regression_record()["status"] == "inconclusive"
    assert session.confirmed_regression_snapshot() is None

    # 3. Append confirmed record
    SessionStore._append(
        reg_path,
        {
            "status": "confirmed",
            "reason": "base_failed_repaired_passed",
            "session_id": session.session_id,
            "base_sha": "0" * 40,
            "workspace_sha256": "b" * 64,
            "test_sha256": "t2" * 32,
        },
    )

    assert len(session.regression_records()) == 2
    assert session.latest_regression_record()["status"] == "confirmed"
    assert session.confirmed_regression_snapshot() == "b" * 64

    # 4. Limit parameter
    limited = session.regression_records(limit=1)
    assert len(limited) == 1
    assert limited[0]["status"] == "confirmed"

    # 5. Subsequent failed/rejected run resets confirmed snapshot
    SessionStore._append(
        reg_path,
        {
            "status": "rejected",
            "reason": "both_failed",
            "session_id": session.session_id,
            "base_sha": "0" * 40,
            "workspace_sha256": "c" * 64,
            "test_sha256": "t3" * 32,
        },
    )
    assert len(session.regression_records()) == 3
    assert session.latest_regression_record()["status"] == "rejected"
    assert session.confirmed_regression_snapshot() is None


def test_api_get_workspace_regression_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    client = TestClient(api_module.app)

    # 1. Non-existent session -> 404
    resp = client.get(
        "/api/code/workspaces/nosuchsession/regression",
        params={"repository": str(repo)},
        headers={"Host": "127.0.0.1:8000"},
    )
    assert resp.status_code == 404

    # 2. Existing session with regression record
    session_root = api_module._CODING_WORKSPACES.session_root(repo, "sessreg01")
    session = SessionStore(session_root, "sessreg01")
    reg_path = session.path.with_name(f"{session.session_id}.regression.jsonl")
    SessionStore._append(
        reg_path,
        {
            "status": "confirmed",
            "reason": "base_failed_repaired_passed",
            "session_id": session.session_id,
            "base_sha": "1" * 40,
            "workspace_sha256": "c" * 64,
            "test_sha256": "d" * 64,
            "base_status": "failed",
            "repaired_status": "passed",
        },
    )

    resp = client.get(
        f"/api/code/workspaces/{session.session_id}/regression",
        params={"repository": str(repo)},
        headers={"Host": "127.0.0.1:8000"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "confirmed"
    assert data["reason"] == "base_failed_repaired_passed"
    assert data["workspace_sha256"] == "c" * 64
    assert data["test_sha256"] == "d" * 64


def test_create_pull_request_annotates_regression_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "origin_repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    git(repo, "add", "hello.py")
    git(repo, "commit", "-m", "Initial commit")
    base_sha = git(repo, "rev-parse", "HEAD").strip()

    git(repo, "remote", "add", "origin", str(repo))

    worktree_path = tmp_path / "worktree"
    git(repo, "worktree", "add", "-b", "selfforge/testsession", str(worktree_path), "HEAD")
    (worktree_path / "hello.py").write_text("print('repaired')\n", encoding="utf-8")
    git(worktree_path, "commit", "-am", "Fix issue")

    workspace = CodingWorkspace(
        session_id="testsession",
        repository=repo,
        path=worktree_path,
        branch="selfforge/testsession",
        base_sha=base_sha,
    )

    session = SessionStore(tmp_path / "sessions", "testsession")

    # Record general JUnit verification
    from selfforge_agent.runtime.fingerprint import workspace_fingerprint
    current_fp = workspace_fingerprint(worktree_path)
    session.record_verification(True, workspace_sha256=current_fp)

    def fake_gh(args: list[str], **kwargs: object) -> str:
        if args[:2] == ["pr", "list"]:
            return "[]"
        if args[:2] == ["pr", "create"]:
            return "https://github.com/example/repo/pull/123\n"
        return ""

    # 1. No regression validation recorded
    with patch("selfforge_agent.runtime.delivery._run_gh", side_effect=fake_gh) as mock_gh:
        create_pull_request(workspace, "Fix bug", session_store=session)
        assert mock_gh.called
        gh_cmd = mock_gh.call_args[0][0]
        body_idx = gh_cmd.index("--body") + 1
        body_text = gh_cmd[body_idx]
        assert "Verification for commit" in body_text
        assert "Regression Validation" not in body_text

    # 2. Confirmed regression validation matching current fingerprint
    reg_path = session.path.with_name(f"{session.session_id}.regression.jsonl")
    SessionStore._append(
        reg_path,
        {
            "status": "confirmed",
            "reason": "base_failed_repaired_passed",
            "session_id": session.session_id,
            "base_sha": base_sha,
            "workspace_sha256": current_fp,
            "test_sha256": "f" * 64,
        },
    )

    with patch("selfforge_agent.runtime.delivery._run_gh", side_effect=fake_gh) as mock_gh:
        create_pull_request(workspace, "Fix bug", session_store=session)
        assert mock_gh.called
        gh_cmd = mock_gh.call_args[0][0]
        body_idx = gh_cmd.index("--body") + 1
        body_text = gh_cmd[body_idx]
        assert "Regression Validation**: Confirmed (old commit failed, repaired worktree passed)" in body_text
