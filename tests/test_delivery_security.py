"""Security and lifecycle hardening reproduction tests for delivery workflow (SF-DELIV-01)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.coding.worktree import git
from selfforge_agent.runtime.delivery import (
    DeliveryError,
    _run_gh,
    _verified_commit,
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
    return manager.create(repo, "sessdelivsec01")


class TestCommitWorkspaceInputAndInjection:
    """SF-DELIV-01-A1: commit_workspace parameter gating and git config injection defense."""

    def test_commit_rejects_non_workspace_instance(self):
        for invalid in [None, 12345, "workspace_str", Path("/tmp")]:
            with pytest.raises((TypeError, DeliveryError)):
                commit_workspace(invalid, "valid message")

    def test_commit_rejects_non_string_message(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        for invalid in [None, 123, ["commit", "msg"], {"msg": "commit"}]:
            with pytest.raises((TypeError, DeliveryError)):
                commit_workspace(managed_workspace, invalid)

    def test_commit_rejects_empty_or_whitespace_message(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        for invalid in ["", "   ", "\t\n"]:
            with pytest.raises(DeliveryError, match="[Cc]ommit message must not be empty"):
                commit_workspace(managed_workspace, invalid)

    def test_commit_rejects_null_byte_in_message(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        with pytest.raises((ValueError, DeliveryError)):
            commit_workspace(managed_workspace, "feat: valid prefix\0injected suffix")

    def test_commit_rejects_oversized_message(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        with pytest.raises(DeliveryError, match="[Mm]essage|[Ll]ength|[Bb]ounds"):
            commit_workspace(managed_workspace, "a" * (64 * 1024 + 1))

    def test_commit_rejects_crlf_or_null_in_author_name(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        for invalid in [
            "Injected\nuser.email=evil@example.com",
            "Injected\ruser.name=evil",
            "Injected\r\nuser.name=evil",
            "Injected\0user.name=evil",
        ]:
            with pytest.raises(DeliveryError, match="[Aa]uthor"):
                commit_workspace(managed_workspace, "msg", author_name=invalid)

    def test_commit_rejects_crlf_or_null_in_author_email(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        for invalid in [
            "evil@example.com\n[core]\nsshCommand=touch /tmp/pwned",
            "evil@example.com\r[core]",
            "evil@example.com\r\n[core]",
            "evil@example.com\0extra",
        ]:
            with pytest.raises(DeliveryError, match="[Aa]uthor"):
                commit_workspace(managed_workspace, "msg", author_email=invalid)

    def test_commit_rejects_invalid_author_type(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        with pytest.raises((TypeError, DeliveryError)):
            commit_workspace(managed_workspace, "msg", author_name=123)
        with pytest.raises((TypeError, DeliveryError)):
            commit_workspace(managed_workspace, "msg", author_email=["evil@example.com"])


class TestCommitWorkspaceStagingAndFilesystem:
    """SF-DELIV-01-A2: Staging validation, path traversal, symlink and non-regular file defense."""

    def test_commit_rejects_symlinked_workspace_path(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        manager = CodingWorkspaceManager(tmp_path / "workspaces")
        ws = manager.create(repo, "sesssym01")

        # Create a symlink to ws.path
        sym_ws_path = tmp_path / "sym_ws"
        sym_ws_path.symlink_to(ws.path, target_is_directory=True)

        fake_ws = CodingWorkspace(
            session_id=ws.session_id,
            path=sym_ws_path,
            branch=ws.branch,
            base_sha=ws.base_sha,
            repository=ws.repository,
        )
        (ws.path / "app.py").write_text("VALUE = 2\n")
        with pytest.raises(DeliveryError, match="[Ss]ymlink"):
            commit_workspace(fake_ws, "should reject symlinked workspace path")

    def test_commit_ignores_or_rejects_fifo(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        fifo_path = managed_workspace.path / "test.fifo"
        try:
            os.mkfifo(fifo_path)
        except (AttributeError, OSError):
            pytest.skip("FIFO not supported on this OS")

        result = commit_workspace(managed_workspace, "feat: update app with fifo present")
        assert "app.py" in result.files_committed
        assert "test.fifo" not in result.files_committed
        tracked = git(managed_workspace.path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        assert "test.fifo" not in tracked

    def test_commit_validates_commit_sha_format(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        result = commit_workspace(managed_workspace, "feat: check sha format")
        assert len(result.commit_sha) == 40
        assert all(c in "0123456789abcdef" for c in result.commit_sha)


class TestRevertWorkspaceSafetyAndEvidence:
    """SF-DELIV-01-A3: Revert parameter gating, evidence invalidation atomicity, and symlink safety."""

    def test_revert_rejects_non_workspace_instance(self):
        for invalid in [None, 12345, "workspace_str", Path("/tmp")]:
            with pytest.raises((TypeError, DeliveryError)):
                revert_workspace(invalid)

    def test_revert_rejects_invalid_session_type(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        for invalid in [123, "not_a_session_store", ["session"]]:
            with pytest.raises((TypeError, DeliveryError)):
                revert_workspace(managed_workspace, session=invalid)

    def test_revert_rejects_symlinked_workspace_path(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        manager = CodingWorkspaceManager(tmp_path / "workspaces")
        ws = manager.create(repo, "sesssymrevert")

        sym_ws_path = tmp_path / "sym_revert_ws"
        sym_ws_path.symlink_to(ws.path, target_is_directory=True)
        fake_ws = CodingWorkspace(
            session_id=ws.session_id,
            path=sym_ws_path,
            branch=ws.branch,
            base_sha=ws.base_sha,
            repository=ws.repository,
        )
        with pytest.raises(DeliveryError, match="[Ss]ymlink"):
            revert_workspace(fake_ws)

    def test_revert_does_not_delete_target_of_untracked_symlink(self, managed_workspace, tmp_path):
        external_target = tmp_path / "external_critical.txt"
        external_target.write_text("critical secret data\n")

        link = managed_workspace.path / "sym_leak.txt"
        link.symlink_to(external_target)

        result = revert_workspace(managed_workspace)
        assert not link.exists()
        assert not link.is_symlink()
        assert external_target.exists()
        assert external_target.read_text() == "critical secret data\n"
        assert "sym_leak.txt" in result.reverted_files


class TestCreatePullRequestAndGHOps:
    """SF-DELIV-01-A4: create_pull_request input gating, option injection defense, and _run_gh bounds."""

    def test_create_pr_rejects_non_workspace_instance(self):
        for invalid in [None, 12345, "workspace_str"]:
            with pytest.raises((TypeError, DeliveryError)):
                create_pull_request(invalid, "PR Title")

    def test_create_pr_rejects_non_string_title(self, managed_workspace):
        for invalid in [None, 123, ["title"], {"title": "str"}]:
            with pytest.raises((TypeError, DeliveryError)):
                create_pull_request(managed_workspace, invalid)

    def test_create_pr_rejects_empty_or_whitespace_title(self, managed_workspace):
        for invalid in ["", "   ", "\t\n"]:
            with pytest.raises(DeliveryError, match="[Tt]itle must not be empty"):
                create_pull_request(managed_workspace, invalid)

    def test_create_pr_rejects_crlf_or_null_in_title(self, managed_workspace):
        for invalid in ["Title\nSecond Line", "Title\r\nSecond Line", "Title\0NullByte"]:
            with pytest.raises(DeliveryError, match="[Tt]itle"):
                create_pull_request(managed_workspace, invalid)

    def test_create_pr_rejects_oversized_title(self, managed_workspace):
        with pytest.raises(DeliveryError, match="[Tt]itle|[Ll]ength|[Bb]ounds"):
            create_pull_request(managed_workspace, "a" * 2000)

    def test_create_pr_rejects_oversized_or_null_body(self, managed_workspace):
        with pytest.raises(DeliveryError, match="[Bb]ody|[Nn]ull"):
            create_pull_request(managed_workspace, "Valid Title", body="body with \0 null")

        with pytest.raises(DeliveryError, match="[Bb]ody|[Ll]ength|[Bb]ounds"):
            create_pull_request(managed_workspace, "Valid Title", body="x" * (300 * 1024))

    def test_create_pr_rejects_invalid_draft_type(self, managed_workspace):
        for invalid in [1, 0, "true", "false", [True]]:
            with pytest.raises((TypeError, DeliveryError)):
                create_pull_request(managed_workspace, "Valid Title", draft=invalid)

    def test_create_pr_rejects_option_injection_in_base_branch(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        commit_workspace(managed_workspace, "commit ahead")
        for invalid in ["--base-injection", "-f", "--flag", "base/with space", "base\0null"]:
            with pytest.raises(DeliveryError, match="[Bb]ase branch|[Bb]ranch"):
                create_pull_request(managed_workspace, "Valid Title", base_branch=invalid)

    def test_create_pr_rejects_option_injection_in_remote(self, managed_workspace):
        (managed_workspace.path / "app.py").write_text("VALUE = 2\n")
        commit_workspace(managed_workspace, "commit ahead")
        for invalid in ["--upload-pack=evil", "-v", "remote/with space", "remote\0null"]:
            with pytest.raises(DeliveryError, match="[Rr]emote"):
                create_pull_request(managed_workspace, "Valid Title", remote=invalid)

    def test_run_gh_rejects_invalid_or_non_positive_timeout(self, tmp_path):
        for invalid in [0, -5, "60", None, float("inf"), float("nan")]:
            with pytest.raises((TypeError, DeliveryError)):
                _run_gh(["version"], cwd=tmp_path, timeout=invalid)

    def test_run_gh_rejects_invalid_cwd(self, tmp_path):
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")
        with pytest.raises((TypeError, DeliveryError)):
            _run_gh(["version"], cwd=file_path)

        with pytest.raises((TypeError, DeliveryError)):
            _run_gh(["version"], cwd=tmp_path / "does_not_exist")

    def test_verified_commit_rejects_mismatched_session_id(self, managed_workspace, tmp_path):
        session_root = tmp_path / "sessions"
        session_root.mkdir()
        other_session = SessionStore(session_root, "diffsessid123")
        commit_sha = git(managed_workspace.path, "rev-parse", "HEAD").strip()
        other_session.record_verification(True, workspace_sha256="a" * 64)
        assert _verified_commit(managed_workspace, other_session, commit_sha) is False

    def test_verified_commit_rejects_invalid_commit_sha(self, managed_workspace, tmp_path):
        session = SessionStore(tmp_path / "sessions", managed_workspace.session_id)
        for invalid_sha in ["not-a-sha", "--diff-filter=D", "1234", "a" * 39, "a" * 41, "g" * 40]:
            assert _verified_commit(managed_workspace, session, invalid_sha) is False


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_gh_output_limit_stops_real_producer(tmp_path, monkeypatch, stream):
    import os

    import selfforge_agent.runtime.delivery as module

    marker = tmp_path / 'finished'
    executable = tmp_path / 'gh'
    executable.write_text('#!/usr/bin/env python3\nimport os\n'
                          f'os.write({1 if stream == "stdout" else 2}, b"x"*1000000)\n'
                          f'open({str(marker)!r}, "w").close()\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    monkeypatch.setattr(module, 'MAX_GH_OUTPUT_BYTES', 1024)
    with pytest.raises(DeliveryError):
        _run_gh(['version'], cwd=tmp_path)
    assert not marker.exists()


def test_large_valid_pr_body_crosses_real_cli_boundary(managed_workspace, tmp_path, monkeypatch):
    import os

    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    git(managed_workspace.repository, 'remote', 'add', 'origin', str(remote))
    (managed_workspace.path / 'app.py').write_text('VALUE = 2\n')
    commit_workspace(managed_workspace, 'change fixture')
    executable = tmp_path / 'gh'
    executable.write_text('''#!/usr/bin/env python3
import pathlib,sys,stat
args = sys.argv[1:]
if args[:2] == ['pr', 'list']:
    print('[]')
else:
    if '--body-file' in args:
        path = pathlib.Path(args[args.index('--body-file')+1])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        body = path.read_text()
    else:
        body = args[args.index('--body')+1]
    assert body == 'x'*200000
    print('https://github.com/fixture/repo/pull/1')
''')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    result = create_pull_request(managed_workspace, 'Fixture', body='x'*200000)
    assert result.pr_number == 1
