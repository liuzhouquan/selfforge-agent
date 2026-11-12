"""Tests for SF-TEST-01 independent regression validation of Coding fixes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import git
from fastapi.testclient import TestClient

from selfforge_agent import cli
from selfforge_agent.api import app as api_module
from selfforge_agent.coding import regression_validation as reg_module
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.verification.runner import TestRunner

IMAGE_ID = "sha256:" + "a" * 64
OTHER_IMAGE_ID = "sha256:" + "b" * 64


def _init_buggy_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_existing.py").write_text(
        "def test_sanity():\n    assert 1 == 1\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial buggy commit")
    return repo, git(repo, "rev-parse", "HEAD")


def _setup_docker_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_id: str = "fixsess01",
    sandbox: str = "docker",
    image_id: str = IMAGE_ID,
    available_images: set[str] | None = None,
    apply_fix: bool = True,
) -> tuple[Path, str, CodingWorkspaceManager, object, SessionStore]:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("SELFFORGE_LOCKS_ROOT", str(tmp_path / "locks"))
    repo, base_sha = _init_buggy_repo(tmp_path)
    manager = CodingWorkspaceManager(root=tmp_path / "worktrees")
    workspace = manager.create(repo, session_id)
    session_root = manager.session_root(repo, session_id)
    session = SessionStore(session_root, session_id)
    session.record_message({"role": "user", "content": "Fix addition"})
    if sandbox == "docker":
        session.record_environment({"version": 1, "sandbox": "docker", "image_id": image_id})
    elif sandbox == "host":
        session.record_environment({"version": 1, "sandbox": "host", "image_id": ""})

    if apply_fix:
        (workspace.path / "calculator.py").write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(cli, "CodingWorkspaceManager", lambda: manager)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", manager)
    from selfforge_agent.api.coding_jobs import CodingJobManager

    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))

    valid_images = {IMAGE_ID} if available_images is None else available_images

    def fake_resolve(ref: str, timeout: float = 10) -> str:
        assert timeout > 0
        if ref not in valid_images:
            raise ValueError("Docker image is unavailable locally")
        return ref

    monkeypatch.setattr(reg_module, "resolve_docker_image", fake_resolve)
    return repo, base_sha, manager, workspace, session


class _LocalSnapshotDockerRunner:
    """Runs real pytest inside isolated temp snapshots without requiring a local Docker daemon."""

    calls: ClassVar[list[tuple[Path, str, str, float]]] = []

    def __init__(self, image: str = "selfforge-agent-sandbox:latest") -> None:
        self.image = image

    def run(
        self,
        repository: Path,
        *,
        trusted: bool = False,
        timeout: float = 60,
        test_path: str = "tests",
        keyword: str | None = None,
    ):
        self.calls.append((repository, self.image, test_path, timeout))
        result = TestRunner().run(
            repository,
            trusted=True,
            timeout=timeout,
            test_path=test_path,
            keyword=keyword,
        )
        return result.model_copy(
            update={"environment": f"docker; image={self.image}; network=none"}
        )


def test_confirmed_regression_via_service_cli_and_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base_sha, _manager, workspace, session = _setup_docker_session(tmp_path, monkeypatch)
    _LocalSnapshotDockerRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)

    secret_token = "SECRET_TEST_BODY_TOKEN_DO_NOT_ECHO"
    candidate_code = (
        "from calculator import add\n\n"
        f"# {secret_token}\n"
        "def test_addition_regression():\n"
        "    assert add(2, 3) == 5\n"
    )
    candidate_file = repo / "candidate_regression.py"
    candidate_file.write_text(candidate_code, encoding="utf-8")
    expected_test_sha = hashlib.sha256(candidate_code.encode("utf-8")).hexdigest()

    # Record prior unverified state to verify read-only regression validation never promotes it.
    session.record_status("unverified")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "selfforge-agent",
            "test-regression",
            "--repo",
            str(repo),
            "--session",
            workspace.session_id,
            "--test-file",
            "candidate_regression.py",
            "--timeout",
            "30",
        ],
    )
    exit_code = cli.main()
    assert exit_code == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["status"] == "confirmed"
    assert cli_payload["reason"] == "base_failed_repaired_passed"
    assert cli_payload["base_sha"] == base_sha
    assert cli_payload["test_sha256"] == expected_test_sha
    assert len(cli_payload["workspace_sha256"]) == 64
    assert cli_payload["base_result"]["status"] == "failed"
    assert cli_payload["base_result"]["failed"] == 1
    assert cli_payload["base_result"]["errors"] == 0
    assert cli_payload["repaired_result"]["status"] == "passed"
    assert cli_payload["repaired_result"]["passed"] == 1
    assert secret_token not in json.dumps(cli_payload)
    assert "stdout" not in cli_payload["base_result"]
    assert "stderr" not in cli_payload["base_result"]
    assert "failure_details" not in cli_payload["base_result"]

    # Both snapshots ran in temp directories using only the session-bound image ID, never workspace.path.
    assert len(_LocalSnapshotDockerRunner.calls) == 2
    assert all(call[0] != workspace.path and call[0] != repo for call in _LocalSnapshotDockerRunner.calls)
    assert all(call[1] == IMAGE_ID for call in _LocalSnapshotDockerRunner.calls)

    # Candidate never entered the user worktree, and Coding session completion status was NOT auto-granted.
    assert not (workspace.path / "candidate_regression.py").exists()
    assert not list(workspace.path.glob("test_selfforge_*"))
    _, current_status = session.load()
    assert current_status == "unverified"
    assert session.verification_passed() is False

    # Same contract via HTTP API
    client = TestClient(api_module.app)
    resp = client.post(
        f"/api/code/workspaces/{workspace.session_id}/validate-regression",
        json={
            "repository": str(repo),
            "test_file": "candidate_regression.py",
            "timeout": 30,
        },
    )
    assert resp.status_code == 200, resp.text
    api_payload = resp.json()
    assert api_payload["status"] == "confirmed"
    assert api_payload["reason"] == "base_failed_repaired_passed"
    assert api_payload["base_sha"] == base_sha
    assert api_payload["test_sha256"] == expected_test_sha
    assert api_payload["workspace_sha256"] == cli_payload["workspace_sha256"]
    assert api_payload["base_result"]["failed"] == 1
    assert api_payload["repaired_result"]["passed"] == 1
    assert secret_token not in resp.text

    # Separate metadata-only trace and .regression.jsonl recorded without secret source leakage.
    trace_lines = session.trace_path.read_text(encoding="utf-8").splitlines()
    assert len(trace_lines) >= 2
    assert secret_token not in session.trace_path.read_text(encoding="utf-8")
    reg_log = session.path.with_name(f"{workspace.session_id}.regression.jsonl")
    assert reg_log.is_file()
    assert secret_token not in reg_log.read_text(encoding="utf-8")
    assert str(repo) not in reg_log.read_text(encoding="utf-8")
    assert str(workspace.path) not in reg_log.read_text(encoding="utf-8")


def test_confirmed_regression_from_workspace_candidate_and_exact_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _, workspace, _ = _setup_docker_session(tmp_path, monkeypatch)
    _LocalSnapshotDockerRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)

    base_code = "from calculator import add\n\ndef test_boundary():\n    assert add(4, 5) == 9\n"
    pad_len = reg_module.MAX_TEST_CANDIDATE_BYTES - len(base_code.encode("utf-8"))
    exact_code = base_code + ("#" * pad_len)
    assert len(exact_code.encode("utf-8")) == reg_module.MAX_TEST_CANDIDATE_BYTES

    ws_tests = workspace.path / "tests"
    ws_tests.mkdir(exist_ok=True)
    candidate = ws_tests / "test_ws_regression.py"
    candidate.write_text(exact_code, encoding="utf-8")

    report = reg_module.validate_workspace_regression(
        repo,
        workspace.session_id,
        "tests/test_ws_regression.py",
        timeout=30,
        workspace_manager=api_module._CODING_WORKSPACES,
    )
    assert report.status == "confirmed"
    assert report.reason == "base_failed_repaired_passed"
    assert len(_LocalSnapshotDockerRunner.calls) == 2


@pytest.mark.parametrize(
    ("case_name", "apply_fix", "test_code", "expected_status", "expected_reason", "expected_exit"),
    [
        (
            "both_passed",
            True,
            "def test_always_true():\n    assert 1 + 1 == 2\n",
            "inconclusive",
            "both_passed",
            1,
        ),
        (
            "repaired_failed",
            False,
            "from calculator import add\n\ndef test_bug_still_present():\n    assert add(2, 3) == 5\n",
            "inconclusive",
            "repaired_failed",
            1,
        ),
        (
            "base_passed_repaired_failed",
            True,
            "from calculator import add\n\ndef test_old_buggy_behavior():\n    assert add(5, 2) == 3\n",
            "inconclusive",
            "repaired_failed",
            1,
        ),
        (
            "empty_file",
            True,
            "",
            "error",
            "empty_or_all_skipped",
            2,
        ),
        (
            "comment_only",
            True,
            "# only a comment\n",
            "error",
            "empty_or_all_skipped",
            2,
        ),
        (
            "all_skip",
            True,
            "import pytest\n\n@pytest.mark.skip(reason='not ready')\ndef test_skipped():\n    assert False\n",
            "error",
            "empty_or_all_skipped",
            2,
        ),
        (
            "partial_skip_on_repaired",
            True,
            (
                "import pytest\nfrom calculator import add\n\n"
                "def test_real():\n    assert add(2, 3) == 5\n\n"
                "@pytest.mark.skip(reason='partial skip')\n"
                "def test_extra():\n    assert False\n"
            ),
            "inconclusive",
            "repaired_had_skipped_tests",
            1,
        ),
        (
            "syntax_error",
            True,
            "def broken(:\n    pass\n",
            "error",
            "collection_or_runtime_error",
            2,
        ),
        (
            "import_error",
            True,
            "from nonexistent_module_xyz import missing\n\ndef test_nope():\n    assert missing()\n",
            "error",
            "collection_or_runtime_error",
            2,
        ),
    ],
)
def test_non_confirming_outcomes_preserve_runner_status_and_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case_name: str,
    apply_fix: bool,
    test_code: str,
    expected_status: str,
    expected_reason: str,
    expected_exit: int,
) -> None:
    clean_suffix = "".join(ch for ch in case_name if ch.isalnum())[:12]
    repo, _, _, workspace, _ = _setup_docker_session(
        tmp_path, monkeypatch, session_id=f"sess{clean_suffix}", apply_fix=apply_fix
    )
    _LocalSnapshotDockerRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)

    secret_marker = f"PRIVATE_MARKER_{case_name}"
    (repo / "candidate.py").write_text(f"{test_code}\n# {secret_marker}\n" if test_code else "", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "selfforge-agent",
            "test-regression",
            "--repo",
            str(repo),
            "--session",
            workspace.session_id,
            "--test-file",
            "candidate.py",
            "--timeout",
            "30",
        ],
    )
    exit_code = cli.main()
    assert exit_code == expected_exit
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["status"] == expected_status
    assert cli_payload["reason"] == expected_reason
    assert secret_marker not in json.dumps(cli_payload)

    # Verify real Runner status/counts are retained without fabrication.
    base_res = cli_payload["base_result"]
    repaired_res = cli_payload["repaired_result"]
    if expected_reason == "empty_or_all_skipped":
        assert base_res["status"] == "error"
        if case_name == "all_skip":
            assert base_res["skipped"] == 1
        else:
            assert base_res["passed"] == 0 and base_res["exit_code"] == 5
    elif expected_reason == "collection_or_runtime_error":
        assert base_res["errors"] >= 1
        assert base_res["exit_code"] != 0
    elif case_name == "partial_skip_on_repaired":
        assert repaired_res["passed"] == 1
        assert repaired_res["skipped"] == 1

    # API parity check.
    client = TestClient(api_module.app)
    resp = client.post(
        f"/api/code/workspaces/{workspace.session_id}/validate-regression",
        json={"repository": str(repo), "test_file": "candidate.py", "timeout": 30},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == expected_status
    assert resp.json()["reason"] == expected_reason


def test_base_import_error_only_cannot_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, manager, workspace, _ = _setup_docker_session(tmp_path, monkeypatch)
    _LocalSnapshotDockerRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)

    # Helper module exists only in repaired workspace, so base raises ImportError (errors == 1).
    (workspace.path / "new_helper.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    (repo / "test_import_diff.py").write_text(
        "from new_helper import double\n\ndef test_double():\n    assert double(3) == 6\n",
        encoding="utf-8",
    )

    report = reg_module.validate_workspace_regression(
        repo,
        workspace.session_id,
        "test_import_diff.py",
        timeout=30,
        workspace_manager=manager,
    )
    assert report.status == "error"
    assert report.reason == "collection_or_runtime_error"
    assert report.base_result is not None and report.base_result.errors >= 1
    assert report.repaired_result is not None and report.repaired_result.passed == 1


def test_timeout_yields_error_status_and_cli_exit_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, _, _, workspace, _ = _setup_docker_session(tmp_path, monkeypatch)
    _LocalSnapshotDockerRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)

    (repo / "test_hang.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "selfforge-agent",
            "test-regression",
            "--repo",
            str(repo),
            "--session",
            workspace.session_id,
            "--test-file",
            "test_hang.py",
            "--timeout",
            "2",
        ],
    )
    exit_code = cli.main()
    captured = capsys.readouterr()
    assert exit_code == 2, captured.err
    assert captured.out.strip(), f"stdout was empty; stderr={captured.err!r}"
    payload = json.loads(captured.out)
    assert payload["status"] == "error", captured.err
    assert payload["reason"] == "timeout"
    assert payload["base_result"] is not None
    assert payload["base_result"]["status"] == "timeout"
    assert payload["repaired_result"] is None


def test_preflight_rejections_before_runner_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, _, manager, workspace, _session = _setup_docker_session(tmp_path, monkeypatch)
    calls: list[tuple] = []

    class RecordingRunner:
        def __init__(self, image: str = "") -> None:
            self.image = image

        def run(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Runner must never execute on preflight failure")

    monkeypatch.setattr(reg_module, "DockerTestRunner", RecordingRunner)
    valid_candidate = repo / "valid.py"
    valid_candidate.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    outside_file = tmp_path / "outside.py"
    outside_file.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / "notes.txt").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / "upper.PY").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    git_hook = repo / ".git" / "hooks" / "test_hook.py"
    git_hook.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "cached.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / "oversized.py").write_text("x = 1\n" + "#" * (reg_module.MAX_TEST_CANDIDATE_BYTES + 1), encoding="utf-8")
    (repo / "binary.py").write_bytes(b"\xff\xfe\x00def test_bin(): pass\n")
    (repo / "dir_named.py").mkdir()
    link_file = repo / "link_to_outside.py"
    link_file.symlink_to(outside_file)
    link_broken = repo / "broken_link.py"
    link_broken.symlink_to(repo / "does_not_exist.py")
    outside_dir = tmp_path / "ext_dir"
    outside_dir.mkdir()
    (outside_dir / "nested.py").write_text("def test_n():\n    assert True\n", encoding="utf-8")
    (repo / "symlink_dir").symlink_to(outside_dir)

    client = TestClient(api_module.app)

    def _assert_rejected(
        target_repo: Path,
        target_session: str,
        test_arg: str,
        *,
        expected_http: int | set[int],
        timeout_arg: object = 30,
    ) -> None:
        resp = client.post(
            f"/api/code/workspaces/{target_session}/validate-regression",
            json={"repository": str(target_repo), "test_file": test_arg, "timeout": timeout_arg},
        )
        allowed = {expected_http} if isinstance(expected_http, int) else expected_http
        assert resp.status_code in allowed, (test_arg, resp.status_code, resp.text)

        if not isinstance(timeout_arg, bool):
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "selfforge-agent",
                    "test-regression",
                    "--repo",
                    str(target_repo),
                    "--session",
                    target_session,
                    "--test-file",
                    test_arg,
                    "--timeout",
                    str(timeout_arg),
                ],
            )
            rc = cli.main()
            assert rc != 0 and rc == 2, (test_arg, rc)
            capsys.readouterr()

    # Path/content preflight rejections -> 400 / 422 / exit 2
    for bad_path in (
        "../outside.py",
        str(outside_file),
        "notes.txt",
        "upper.PY",
        ".env",
        ".env.local",
        ".git/hooks/test_hook.py",
        "__pycache__/cached.py",
        "oversized.py",
        "binary.py",
        "dir_named.py",
        "link_to_outside.py",
        "broken_link.py",
        "symlink_dir/nested.py",
        str(repo / "symlink_dir" / "nested.py"),
    ):
        _assert_rejected(repo, workspace.session_id, bad_path, expected_http=400)

    # Missing candidate file -> 404
    _assert_rejected(repo, workspace.session_id, "missing_test.py", expected_http=404)

    # Timeout bounds & bool rejection
    for bad_timeout in (0, -5, 181, True):
        _assert_rejected(repo, workspace.session_id, "valid.py", expected_http={400, 422}, timeout_arg=bad_timeout)

    # Symlinked repo directory -> 400
    repo_link = tmp_path / "repo_symlink"
    repo_link.symlink_to(repo)
    _assert_rejected(repo_link, workspace.session_id, "valid.py", expected_http=400)

    # Cross-repo mismatch / unknown / invalid session -> 404 or 400
    other_repo, _ = _init_buggy_repo(tmp_path / "other")
    (other_repo / "ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _assert_rejected(other_repo, workspace.session_id, "ok.py", expected_http=404)
    _assert_rejected(other_repo, "missing99", "ok.py", expected_http=404)
    _assert_rejected(repo, "bad!session", "valid.py", expected_http=400)

    # Concurrent workspace lock conflict via both CodingWorkspaceManager.guard and CodingJobManager
    with manager.guard(workspace.path, message="Coding workspace is busy running another job"):
        _assert_rejected(repo, workspace.session_id, "valid.py", expected_http=409)
    with api_module._CODING_JOBS.workspace_guard(workspace.path):
        _assert_rejected(repo, workspace.session_id, "valid.py", expected_http=409)

    # Missing local Docker image -> 400
    monkeypatch.setattr(
        reg_module,
        "resolve_docker_image",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("No such image")),
    )
    _assert_rejected(repo, workspace.session_id, "valid.py", expected_http=400)

    # Host-bound, unbound, and corrupt sessions -> 400
    host_repo, _, _, host_ws, _ = _setup_docker_session(
        tmp_path / "host_case", monkeypatch, session_id="hostsess01", sandbox="host"
    )
    (host_repo / "valid.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _assert_rejected(host_repo, host_ws.session_id, "valid.py", expected_http=400)

    unbound_repo, _, _, unbound_ws, _ = _setup_docker_session(
        tmp_path / "unbound_case", monkeypatch, session_id="unbound01", sandbox="none"
    )
    (unbound_repo / "valid.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _assert_rejected(unbound_repo, unbound_ws.session_id, "valid.py", expected_http=400)

    corrupt_repo, _, _, corrupt_ws, corrupt_sess = _setup_docker_session(
        tmp_path / "corrupt_case", monkeypatch, session_id="corrupt01"
    )
    (corrupt_repo / "valid.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    corrupt_sess.path.write_text("{broken json\n", encoding="utf-8")
    _assert_rejected(corrupt_repo, corrupt_ws.session_id, "valid.py", expected_http=400)

    # Localhost-only & cross-origin security checks on API
    cross = client.post(
        f"/api/code/workspaces/{workspace.session_id}/validate-regression",
        json={"repository": str(repo), "test_file": "valid.py", "timeout": 30},
        headers={"Origin": "https://evil.example"},
    )
    assert cross.status_code == 403

    remote_client = TestClient(api_module.app, client=("203.0.113.10", 12345))
    remote = remote_client.post(
        f"/api/code/workspaces/{workspace.session_id}/validate-regression",
        json={"repository": str(repo), "test_file": "valid.py", "timeout": 30},
    )
    assert remote.status_code == 403
    assert calls == []


def test_isolation_tampering_cleanup_and_session_evidence_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, manager, workspace, session = _setup_docker_session(tmp_path, monkeypatch)

    # Pre-existing completed evidence in SessionStore must remain untouched.
    session.record_status("completed")
    session.record_verification(True, workspace_sha256="c" * 64)
    before_session_bytes = session.path.read_text(encoding="utf-8")
    user_note = workspace.path / "notes.tmp"
    user_note.write_text("keep my local worktree note", encoding="utf-8")

    # 1) Cross-snapshot isolation check: side effect in base must never reach repaired or workspace.
    (repo / "test_side_effect.py").write_text(
        "from pathlib import Path\n"
        "from calculator import add\n\n"
        "def test_isolated():\n"
        "    marker = Path('snapshot_marker.txt')\n"
        "    assert not marker.exists()\n"
        "    marker.write_text('mutated', encoding='utf-8')\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reg_module, "DockerTestRunner", _LocalSnapshotDockerRunner)
    isolated_report = reg_module.validate_workspace_regression(
        repo, workspace.session_id, "test_side_effect.py", timeout=30, workspace_manager=manager
    )
    assert isolated_report.status == "confirmed"
    assert not (workspace.path / "snapshot_marker.txt").exists()
    assert not (repo / "snapshot_marker.txt").exists()

    # 2) Mid-run tampering of workspace revokes confirmation.
    (repo / "test_confirm.py").write_text(
        "from calculator import add\n\ndef test_ok():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    class WorkspaceTamperingRunner(_LocalSnapshotDockerRunner):
        def run(self, repository: Path, **kwargs):
            result = super().run(repository, **kwargs)
            if len(self.calls) == 1:
                (workspace.path / "tamper.txt").write_text("edited mid-run", encoding="utf-8")
            return result

    WorkspaceTamperingRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", WorkspaceTamperingRunner)
    ws_tamper_report = reg_module.validate_workspace_regression(
        repo, workspace.session_id, "test_confirm.py", timeout=30, workspace_manager=manager
    )
    assert ws_tamper_report.status == "inconclusive"
    assert ws_tamper_report.reason == "workspace_changed"
    (workspace.path / "tamper.txt").unlink()

    # 3) Mid-run tampering of external candidate file in repo revokes confirmation.
    class CandidateTamperingRunner(_LocalSnapshotDockerRunner):
        def run(self, repository: Path, **kwargs):
            result = super().run(repository, **kwargs)
            if len(self.calls) == 1:
                (repo / "test_confirm.py").write_text("# rewritten\n", encoding="utf-8")
            return result

    CandidateTamperingRunner.calls = []
    monkeypatch.setattr(reg_module, "DockerTestRunner", CandidateTamperingRunner)
    cand_tamper_report = reg_module.validate_workspace_regression(
        repo, workspace.session_id, "test_confirm.py", timeout=30, workspace_manager=manager
    )
    assert cand_tamper_report.status == "inconclusive"
    assert cand_tamper_report.reason == "workspace_changed"

    # 4) Forced exception cleans up temp directories & detached git worktrees, preserves files & evidence.
    class ExplodingRunner:
        def __init__(self, image: str = "") -> None:
            self.image = image

        def run(self, repository: Path, **kwargs):
            raise OSError("Simulated container teardown error")

    before_tmp = {p.name for p in Path(tempfile.gettempdir()).glob("selfforge-regression-*")}
    monkeypatch.setattr(reg_module, "DockerTestRunner", ExplodingRunner)
    err_report = reg_module.validate_workspace_regression(
        repo, workspace.session_id, "test_confirm.py", timeout=30, workspace_manager=manager
    )
    assert err_report.status == "error"
    assert err_report.reason == "snapshot_error"
    after_tmp = {p.name for p in Path(tempfile.gettempdir()).glob("selfforge-regression-*")}
    assert not (after_tmp - before_tmp)

    worktree_list = git(repo, "worktree", "list", "--porcelain")
    worktrees = [
        line.split(" ", 1)[1]
        for line in worktree_list.splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 2  # only main repo + persistent workspace.path
    assert user_note.read_text(encoding="utf-8") == "keep my local worktree note"
    assert session.path.read_text(encoding="utf-8") == before_session_bytes
    assert session.load()[1] == "completed"
    assert session.verification_passed() is True


@pytest.mark.integration
def test_real_docker_regression_validation_confirmed_and_counterexample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = "selfforge-agent-sandbox:latest"
    for probe in (["docker", "info"], ["docker", "image", "inspect", image]):
        try:
            probe_res = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if probe_res.returncode != 0:
            pytest.skip("Docker daemon or sandbox image unavailable")

    real_image_id = reg_module.resolve_docker_image(image)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("SELFFORGE_LOCKS_ROOT", str(tmp_path / "locks"))
    repo, base_sha = _init_buggy_repo(tmp_path)
    manager = CodingWorkspaceManager(root=tmp_path / "worktrees")
    workspace = manager.create(repo, "dockreg01")
    session_root = manager.session_root(repo, workspace.session_id)
    session = SessionStore(session_root, workspace.session_id)
    session.record_message({"role": "user", "content": "Fix add bug"})
    session.record_environment({"version": 1, "sandbox": "docker", "image_id": real_image_id})
    (workspace.path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    monkeypatch.setattr(cli, "CodingWorkspaceManager", lambda: manager)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", manager)

    # Confirm genuine regression inside real Docker containers (network=none).
    (repo / "test_real_docker_reg.py").write_text(
        "from calculator import add\n\ndef test_add_real():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "selfforge-agent",
            "test-regression",
            "--repo",
            str(repo),
            "--session",
            workspace.session_id,
            "--test-file",
            "test_real_docker_reg.py",
            "--timeout",
            "60",
        ],
    )
    rc = cli.main()
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "confirmed"
    assert payload["reason"] == "base_failed_repaired_passed"
    assert payload["base_sha"] == base_sha
    assert payload["base_result"]["failed"] == 1
    assert payload["repaired_result"]["passed"] == 1

    # Negative check via API in real Docker containers.
    (repo / "test_tautology.py").write_text("def test_tautology():\n    assert 1 == 1\n", encoding="utf-8")
    client = TestClient(api_module.app)
    resp = client.post(
        f"/api/code/workspaces/{workspace.session_id}/validate-regression",
        json={"repository": str(repo), "test_file": "test_tautology.py", "timeout": 60},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inconclusive"
    assert resp.json()["reason"] == "both_passed"
