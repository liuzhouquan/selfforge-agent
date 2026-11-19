"""Comprehensive hardening and security defect reproduction tests for delivery workflow (SF-DELIV-02)."""

import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import (
    CommitResult,
    DeliveryError,
    PRResult,
    RevertResult,
    _existing_pull_request,
    _validate_workspace,
    commit_workspace,
    create_pull_request,
    revert_workspace,
)
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace, CodingWorkspaceManager


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Author"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "author@example.com"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "Initial commit"], check=True)
    return path


@pytest.fixture
def managed_workspace(tmp_path: Path) -> CodingWorkspace:
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    return manager.create(repo, "sessdelivsec02")


class TestDeliveryModelsHardening:
    """SF-DELIV-02-A1: Strict Pydantic model validation and normalization."""

    def test_commit_result_validates_commit_sha(self) -> None:
        for invalid_sha in ["not-a-sha", "G" * 40, "-o", "a" * 39, "a" * 41, 12345, ""]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                CommitResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha=invalid_sha,
                    message="Valid message",
                )
        # Uppercase hex should be normalized to lowercase
        valid_res = CommitResult(
            session_id="sess1",
            branch="main",
            commit_sha="A" * 40,
            message="Valid message",
        )
        assert valid_res.commit_sha == "a" * 40

    def test_commit_result_validates_session_id_and_branch(self) -> None:
        for invalid_sess in ["../evil", "", "a" * 100, "bad sess\0", "sess/slash"]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                CommitResult(
                    session_id=invalid_sess,
                    branch="main",
                    commit_sha="a" * 40,
                    message="Valid message",
                )
        for invalid_branch in ["-b", "main..evil", "feat/bad\0", "", "feat//bad", "feat.lock"]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                CommitResult(
                    session_id="sess1",
                    branch=invalid_branch,
                    commit_sha="a" * 40,
                    message="Valid message",
                )

    def test_commit_result_validates_message_and_files(self) -> None:
        for invalid_msg in ["", "   ", "msg\0bad"]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                CommitResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha="a" * 40,
                    message=invalid_msg,
                )
        for invalid_files in [["../evil.py"], ["bad\0file"], ["/abs/path.py"]]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                CommitResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha="a" * 40,
                    message="Valid msg",
                    files_committed=invalid_files,
                )

    def test_pr_result_validates_pr_url_and_number(self) -> None:
        for invalid_url in [
            "https://github.com/../evil/pull/1",
            "javascript:alert(1)",
            "not-a-url",
            "https://github.com/o/r/pull/0",
            "http://github.com/owner/repo/pull/1",
        ]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                PRResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha="a" * 40,
                    pr_url=invalid_url,
                    pr_number=1,
                    title="PR Title",
                )
        for invalid_num in [True, False, 0, -1, "123"]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                PRResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha="a" * 40,
                    pr_url="https://github.com/owner/repo/pull/123",
                    pr_number=invalid_num,
                    title="PR Title",
                )
        for invalid_title in ["", "Title with \n newline", "Title with \0 null"]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                PRResult(
                    session_id="sess1",
                    branch="main",
                    commit_sha="a" * 40,
                    pr_url="https://github.com/owner/repo/pull/123",
                    pr_number=123,
                    title=invalid_title,
                )

    def test_revert_result_validates_paths(self) -> None:
        for invalid_paths in [["../outside.py"], ["bad\0path"], ["/absolute/path"]]:
            with pytest.raises((ValueError, ValidationError, TypeError)):
                RevertResult(
                    session_id="sess1",
                    branch="main",
                    reverted_files=invalid_paths,
                )


class TestWorkspaceAndRefHardening:
    """SF-DELIV-02-A2: Workspace base_sha and git ref validation hardening."""

    def test_validate_workspace_rejects_invalid_base_sha(self, managed_workspace) -> None:
        for invalid_sha in ["not-a-sha", "-o injection", "a" * 39, "a" * 41, "", None]:
            ws = replace(managed_workspace, base_sha=invalid_sha)
            with pytest.raises((DeliveryError, TypeError)):
                _validate_workspace(ws)

    def test_validate_workspace_rejects_malicious_branch(self, managed_workspace) -> None:
        for invalid_branch in [
            "foo..bar",
            "feat//bad",
            "feat.lock",
            "feat/",
            "-b",
            "feat\0bad",
            "feat\nbad",
        ]:
            ws = replace(managed_workspace, branch=invalid_branch)
            with pytest.raises(DeliveryError):
                _validate_workspace(ws)

    def test_create_pr_rejects_same_base_and_head_branch(self, managed_workspace) -> None:
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        commit_workspace(managed_workspace, "Commit ahead")
        with pytest.raises(DeliveryError, match="must not be identical"):
            create_pull_request(managed_workspace, "Valid Title", base_branch=managed_workspace.branch)

    def test_create_pr_rejects_traversal_or_malformed_base_branch(self, managed_workspace) -> None:
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        commit_workspace(managed_workspace, "Commit ahead")
        for bad_base in ["main..evil", "main/bad.lock", "main//bad", "main/"]:
            with pytest.raises(DeliveryError, match="Invalid base branch name"):
                create_pull_request(managed_workspace, "Valid Title", base_branch=bad_base)


class TestGitConfigInjectionAndSessionStoreHardening:
    """SF-DELIV-02-A3: Git config injection defense and session boundary protection."""

    def test_commit_workspace_sanitizes_malicious_repo_git_config(
        self, managed_workspace
    ) -> None:
        # Inject malicious user.name and user.email into repository config file
        cfg = managed_workspace.repository / ".git" / "config"
        cfg.write_text(
            cfg.read_text(encoding="utf-8")
            + "\n[user]\n\tname = attacker\\n[evil]\n\temail = -o injected\n",
            encoding="utf-8",
        )
        (managed_workspace.path / "app.py").write_text("VALUE = 99\n")

        # commit_workspace without author override should safely fallback to default
        result = commit_workspace(managed_workspace, "Test config sanitization")
        assert result.commit_sha
        log_author = git(managed_workspace.path, "log", "-1", "--format=%an <%ae>").strip()
        assert log_author == "selfForge Agent <agent@selfforge.local>"

    def test_revert_workspace_rejects_mismatched_session_store(
        self, managed_workspace, tmp_path
    ) -> None:
        mismatched_session = SessionStore(tmp_path / "sessions", "othersess999")
        (managed_workspace.path / "app.py").write_text("DIRTY = True\n")
        with pytest.raises(DeliveryError, match="SessionStore session_id"):
            revert_workspace(managed_workspace, session=mismatched_session)

    def test_create_pull_request_rejects_mismatched_session_store(
        self, managed_workspace, tmp_path
    ) -> None:
        mismatched_session = SessionStore(tmp_path / "sessions", "othersess999")
        (managed_workspace.path / "app.py").write_text("VALUE = 5\n")
        commit_workspace(managed_workspace, "Commit 5")
        with pytest.raises(DeliveryError, match="SessionStore session_id"):
            create_pull_request(managed_workspace, "Title", session_store=mismatched_session)

    def test_commit_workspace_batches_large_file_lists(
        self, managed_workspace, monkeypatch
    ) -> None:
        # Create 150 untracked files
        for i in range(150):
            (managed_workspace.path / f"mod_{i}.py").write_text(f"X = {i}\n")

        recorded_batches = []
        real_git = git

        def spy_git(repo, *args, **kwargs):
            if len(args) >= 3 and args[0] == "--literal-pathspecs" and args[1] == "add":
                files = args[args.index("--") + 1 :]
                recorded_batches.append(len(files))
            return real_git(repo, *args, **kwargs)

        monkeypatch.setattr("selfforge_agent.runtime.delivery.git", spy_git)

        result = commit_workspace(managed_workspace, "Commit 150 files")
        assert len(result.files_committed) == 150
        assert len(recorded_batches) > 1
        assert all(batch <= 100 for batch in recorded_batches)


class TestExistingPRAndSecurityHardening:
    """SF-DELIV-02-A4: PR query validation, bounding, and temporary file security."""

    def test_existing_pull_request_rejects_invalid_pr_records(
        self, managed_workspace, monkeypatch
    ) -> None:
        bad_records = [
            # pr number <= 0
            json.dumps([{"number": 0, "title": "T", "url": "https://github.com/o/r/pull/0"}]),
            # pr number is boolean
            json.dumps([{"number": True, "title": "T", "url": "https://github.com/o/r/pull/1"}]),
            # title with null byte
            json.dumps([{"number": 1, "title": "T\0bad", "url": "https://github.com/o/r/pull/1"}]),
            # url with traversal
            json.dumps([{"number": 1, "title": "T", "url": "https://github.com/../evil/pull/1"}]),
        ]
        for record_json in bad_records:
            monkeypatch.setattr(
                "selfforge_agent.runtime.delivery._run_gh",
                lambda *args, r=record_json, **kwargs: r,
            )
            with pytest.raises(DeliveryError):
                _existing_pull_request(managed_workspace, "main")

    def test_existing_pull_request_rejects_excessive_pr_list(
        self, managed_workspace, monkeypatch
    ) -> None:
        excessive = [
            {"number": i + 1, "title": f"PR {i}", "url": f"https://github.com/o/r/pull/{i + 1}"}
            for i in range(105)
        ]
        monkeypatch.setattr(
            "selfforge_agent.runtime.delivery._run_gh",
            lambda *args, **kwargs: json.dumps(excessive),
        )
        with pytest.raises(DeliveryError, match="exceeds"):
            _existing_pull_request(managed_workspace, "main")

    def test_create_pull_request_sets_0600_permissions_on_body_file(
        self, managed_workspace, tmp_path, monkeypatch
    ) -> None:
        bare_remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare_remote)], check=True)
        git(managed_workspace.repository, "remote", "add", "origin", str(bare_remote))

        (managed_workspace.path / "app.py").write_text("VALUE = 88\n")
        commit_workspace(managed_workspace, "Large body commit")

        large_body = "b" * 70000
        observed_permissions = []

        def mock_run_gh(args, *, cwd, timeout=60):
            if args[:2] == ["pr", "list"]:
                return "[]"
            if "--body-file" in args:
                idx = args.index("--body-file")
                file_path = args[idx + 1]
                st = os.stat(file_path)
                observed_permissions.append(stat.S_IMODE(st.st_mode))
            return "https://github.com/owner/repo/pull/777\n"

        monkeypatch.setattr("selfforge_agent.runtime.delivery._run_gh", mock_run_gh)

        result = create_pull_request(managed_workspace, "Valid PR", body=large_body)
        assert result.pr_number == 777
        assert len(observed_permissions) == 1
        # Check permissions: 0o600 (owner read and write only)
        assert observed_permissions[0] == 0o600


@pytest.mark.parametrize("result_class,field", [(CommitResult, "files_committed"), (RevertResult, "reverted_files")])
def test_delivery_results_preserve_literal_git_filenames(result_class, field):
    kwargs = {"session_id": "literal", "branch": "main", field: [" file.py "]}
    if result_class is CommitResult:
        kwargs.update(commit_sha="a" * 40, message="Fixture")
    assert getattr(result_class(**kwargs), field) == [" file.py "]


@pytest.mark.parametrize("repo", [".github", "_tools"])
def test_delivery_accepts_legal_github_repository_names(repo):
    url = f"https://github.com/example/{repo}/pull/1"
    result = PRResult(session_id="literal", branch="main", commit_sha="a" * 40, title="Fixture", pr_url=url, pr_number=1)
    assert result.pr_url == url


def test_delivery_preserves_legal_plus_branch():
    result = CommitResult(session_id="literal", branch="feature+fix", commit_sha="a" * 40, message="Fixture")
    assert result.branch == "feature+fix"
