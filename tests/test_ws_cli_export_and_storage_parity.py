"""Regression and contract tests for SF-WS-05 storage parity and fail-closed CLI exports."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.cli import main
from selfforge_agent.coding.regression_validation import (
    RegressionRunSummary,
    RegressionValidationReport,
)
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "app.py").write_text("def val():\n    return 1\n", encoding="utf-8")
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


def test_storage_api_uses_configured_coding_workspaces_manager_consistently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _init_repo(tmp_path)
    configured_root = tmp_path / "configured-worktrees"
    custom_manager = CodingWorkspaceManager(configured_root)
    ws = custom_manager.create(repo, "storage01")
    (ws.path / "artifact.bin").write_bytes(b"a" * 2048)

    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", custom_manager)
    monkeypatch.delenv("SELFFORGE_WORKSPACES_ROOT", raising=False)
    client = TestClient(api_module.app)

    audit_res = client.get("/api/workspaces/audit", params={"repository": str(repo)})
    assert audit_res.status_code == 200
    assert [item["session_id"] for item in audit_res.json()] == ["storage01"]

    for endpoint in ("/api/workspaces/storage", "/api/code/workspaces/storage"):
        res = client.get(endpoint, params={"repository": str(repo), "quota_bytes": 1024})
        assert res.status_code == 200
        payload = res.json()
        assert payload["workspace_count"] == 1
        assert payload["workspaces"][0]["session_id"] == "storage01"
        assert payload["total_bytes"] >= 2048
        assert payload["quota_exceeded"] is True

    bad_quota = client.get("/api/workspaces/storage", params={"repository": str(repo), "quota_bytes": -1})
    assert bad_quota.status_code == 400


def test_storage_api_preserves_env_override_compatibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _init_repo(tmp_path)
    env_root = tmp_path / "env-worktrees"
    monkeypatch.setenv("SELFFORGE_WORKSPACES_ROOT", str(env_root))
    manager = CodingWorkspaceManager()
    manager.create(repo, "envsess01")

    res = TestClient(api_module.app).get("/api/workspaces/storage", params={"repository": str(repo)})
    assert res.status_code == 200
    assert [item["session_id"] for item in res.json()["workspaces"]] == ["envsess01"]


@pytest.mark.parametrize(
    ("status", "reason", "expected_exit"),
    [
        ("confirmed", "base_failed_repaired_passed", 0),
        ("inconclusive", "both_passed", 1),
        ("error", "timeout", 2),
    ],
)
def test_cli_test_regression_writes_output_report_across_statuses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    reason: str,
    expected_exit: int,
) -> None:
    repo = _init_repo(tmp_path)
    out_file = tmp_path / "regression-report.json"
    fake_report = RegressionValidationReport(
        status=status,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        session_id="sess01",
        base_sha="a" * 40,
        workspace_sha256="b" * 64,
        test_sha256="c" * 64,
        base_result=RegressionRunSummary(
            status="failed" if status == "confirmed" else "passed",
            exit_code=1 if status == "confirmed" else 0,
            duration_seconds=0.5,
            passed=0 if status == "confirmed" else 1,
            failed=1 if status == "confirmed" else 0,
            errors=0,
            skipped=0,
        ),
        repaired_result=None
        if status != "confirmed"
        else RegressionRunSummary(
            status="passed",
            exit_code=0,
            duration_seconds=0.4,
            passed=1,
            failed=0,
            errors=0,
            skipped=0,
        ),
    )
    with patch("selfforge_agent.cli.validate_workspace_regression", return_value=fake_report):
        exit_code = main(
            [
                "test-regression",
                "--repo",
                str(repo),
                "--session",
                "sess01",
                "--test-file",
                "tests/test_reg.py",
                "--output",
                str(out_file),
            ]
        )

    assert exit_code == expected_exit
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert file_payload == stdout_payload
    assert file_payload["status"] == status
    assert file_payload["reason"] == reason


def test_cli_trace_supports_json_and_output_while_keeping_legacy_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    store = SessionStore(session_root, "tracexp01")
    store.trace(
        "model_call",
        provider="deepseek",
        model="deepseek-chat",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )
    store.trace("tool", name="run_tests", ok=True)

    with patch("selfforge_agent.runtime.workspace.CodingWorkspaceManager.session_root", return_value=session_root):
        trace_out = tmp_path / "trace.json"
        assert main(["trace", "tracexp01", "--repo", str(repo), "--json", "--output", str(trace_out)]) == 0
        stdout_json = json.loads(capsys.readouterr().out)
        written_json = json.loads(trace_out.read_text(encoding="utf-8"))
        assert written_json == stdout_json
        assert written_json["session_id"] == "tracexp01"
        assert written_json["event_count"] == 2
        assert [ev["event"] for ev in written_json["events"]] == ["model_call", "tool"]

        summary_out = tmp_path / "summary.json"
        assert main(["trace", "tracexp01", "--repo", str(repo), "--summary", "--json", "--output", str(summary_out)]) == 0
        summary_stdout = json.loads(capsys.readouterr().out)
        summary_file = json.loads(summary_out.read_text(encoding="utf-8"))
        assert summary_file == summary_stdout
        assert summary_file["session_id"] == "tracexp01"
        assert summary_file["total_tokens"] == 150
        assert summary_file["prompt_tokens"] == 100
        assert summary_file["completion_tokens"] == 50

        # Legacy text summary preserved when --json omitted
        assert main(["trace", "tracexp01", "--repo", str(repo), "--summary"]) == 0
        legacy_text = capsys.readouterr().out
        assert "Summary for session tracexp01:" in legacy_text
        assert "Total tokens: 150" in legacy_text


def test_cli_outputs_fail_closed_for_symlinks_directories_and_missing_parents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    store = SessionStore(session_root, "safe01")
    store.trace("tool", name="read_file", ok=True)

    target_real = tmp_path / "victim.txt"
    target_real.write_text("KEEP_ME", encoding="utf-8")
    symlink_out = tmp_path / "out-link.json"
    symlink_out.symlink_to(target_real)
    dir_out = tmp_path / "existing-dir"
    dir_out.mkdir()
    missing_parent_out = tmp_path / "missing-dir" / "report.json"

    fake_report = RegressionValidationReport(
        status="confirmed",
        reason="base_failed_repaired_passed",
        session_id="safe01",
        base_sha="a" * 40,
        workspace_sha256="b" * 64,
        test_sha256="c" * 64,
    )

    for bad_path in (symlink_out, dir_out, missing_parent_out):
        with patch("selfforge_agent.runtime.workspace.CodingWorkspaceManager.session_root", return_value=session_root):
            with pytest.raises(SystemExit) as trace_exc:
                main(["trace", "safe01", "--repo", str(repo), "--json", "--output", str(bad_path)])
            assert trace_exc.value.code == 2

        with patch("selfforge_agent.cli.validate_workspace_regression", return_value=fake_report):
            with pytest.raises(SystemExit) as reg_exc:
                main(
                    [
                        "test-regression",
                        "--repo",
                        str(repo),
                        "--session",
                        "safe01",
                        "--test-file",
                        "tests/test_ok.py",
                        "--output",
                        str(bad_path),
                    ]
                )
            assert reg_exc.value.code == 2

        with pytest.raises(SystemExit) as storage_exc:
            main(["workspaces", "storage", "--repo", str(repo), "--output", str(bad_path)])
        assert storage_exc.value.code == 2

    # Ensure FIFO non-regular files are rejected without blocking
    fifo_out = tmp_path / "out.fifo"
    os.mkfifo(fifo_out)
    with patch("selfforge_agent.runtime.workspace.CodingWorkspaceManager.session_root", return_value=session_root):
        with pytest.raises(SystemExit) as fifo_exc:
            main(["trace", "safe01", "--repo", str(repo), "--json", "--output", str(fifo_out)])
        assert fifo_exc.value.code == 2

    assert target_real.read_text(encoding="utf-8") == "KEEP_ME"
    capsys.readouterr()


def test_trace_export_rejects_fifo_replacement_without_blocking(tmp_path: Path) -> None:
    import stat
    import sys

    repo = _init_repo(tmp_path)
    output = tmp_path / 'output.json'
    output.write_text('keep')
    script = r'''
import os, sys
from pathlib import Path
from selfforge_agent import cli
real_open = os.open
target = Path(sys.argv[2])
def replace_before_open(path, flags, *args, **kwargs):
    if Path(path) == target:
        target.unlink()
        os.mkfifo(target)
    return real_open(path, flags, *args, **kwargs)
os.open = replace_before_open
cli.main(['trace', 'raceexport', '--repo', sys.argv[1], '--json', '--output', str(target)])
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(repo), str(output)],
        capture_output=True, text=True, timeout=3, check=False,
    )
    assert result.returncode == 2
    assert stat.S_ISFIFO(output.lstat().st_mode)


def test_export_checks_descriptor_before_truncation(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from selfforge_agent import cli

    output = tmp_path / 'preserved.json'
    output.write_text('keep existing content')
    monkeypatch.setattr(cli.os, 'fstat', lambda _fd: SimpleNamespace(st_mode=0))
    with pytest.raises(ValueError, match='regular file'):
        cli._write_cli_output(output, '{}')
    assert output.read_text() == 'keep existing content'
