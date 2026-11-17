"""Security, fail-closed boundaries, and symlink defense tests for coding jobs (SF-JOB-01)."""

import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from selfforge_agent.api.coding_jobs import (
    CodingJobManager,
    DurableCancel,
    _write_cancel_marker,
)
from selfforge_agent.runtime.session import SessionStore


def _make_dummy_agent(workspace: Path, sessions: Path) -> SimpleNamespace:
    return SimpleNamespace(
        tools=SimpleNamespace(root=workspace),
        session=SessionStore(sessions),
        cancel=Event(),
        run=lambda task, resume=False: SimpleNamespace(
            session_id="dummy", status="completed", answer="ok", steps=1, tools_used=0, error=""
        ),
    )


class TestCancelMarkerAndDurableCancelSecurity:
    """Acceptance criteria SF-JOB-01-A1: Cancellation marker and DurableCancel defense."""

    def test_write_cancel_marker_rejects_non_path(self):
        for invalid in ["marker", 12345, None, ["marker"]]:
            with pytest.raises(TypeError, match="[Pp]ath"):
                _write_cancel_marker(invalid)  # type: ignore[arg-type]

    def test_write_cancel_marker_rejects_null_byte(self):
        with pytest.raises(ValueError, match="null byte"):
            _write_cancel_marker(Path("cancel\0marker"))

    def test_write_cancel_marker_rejects_symlinked_parent_directory(self, tmp_path: Path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        sym_dir = tmp_path / "sym_dir"
        sym_dir.symlink_to(real_dir, target_is_directory=True)
        marker = sym_dir / "marker"

        with pytest.raises(ValueError, match="[Ss]ymlink"):
            _write_cancel_marker(marker)

    def test_write_cancel_marker_rejects_fifo(self, tmp_path: Path):
        fifo_path = tmp_path / "fifo_marker"
        os.mkfifo(fifo_path)
        with pytest.raises(ValueError, match="regular file"):
            _write_cancel_marker(fifo_path)

    def test_durable_cancel_ignores_symlinked_marker(self, tmp_path: Path):
        real_file = tmp_path / "real_file"
        real_file.write_text("cancel", encoding="utf-8")
        sym_marker = tmp_path / "sym_marker"
        sym_marker.symlink_to(real_file)

        dc = DurableCancel(sym_marker, Event())
        assert not dc.is_set(), "DurableCancel must not consider a symlinked marker as set"


class TestJobRecordSecurity:
    """Acceptance criteria SF-JOB-01-A2: Job record serialization, loading, and state integrity."""

    def test_record_path_rejects_non_string_or_invalid_job_id(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in [None, 12345, [], {}, "bad_id", "0123456789a", "0123456789abc"]:
            with pytest.raises(KeyError):
                mgr._record_path(invalid)  # type: ignore[arg-type]

    def test_write_record_rejects_non_dict_or_missing_job_id(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in ["not_a_dict", 12345, None, []]:
            with pytest.raises((TypeError, ValueError)):
                mgr._write_record(invalid)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="[Jj]ob_id"):
            mgr._write_record({"state": "running"})

    def test_write_record_rejects_symlinked_parent_directory(self, tmp_path: Path):
        real_records = tmp_path / "real_records"
        real_records.mkdir()
        sym_jobs = tmp_path / "sym_jobs"
        sym_jobs.mkdir()
        (sym_jobs / "records").symlink_to(real_records, target_is_directory=True)
        (sym_jobs / "cancel").mkdir()

        mgr = CodingJobManager(root=sym_jobs)
        record = {
            "job_id": "0123456789ab",
            "session_id": "s1",
            "session_root": str(tmp_path),
            "state": "running",
            "workspace_path": str(tmp_path),
            "updated_at": 1000.0,
        }
        with pytest.raises(ValueError, match="[Ss]ymlink"):
            mgr._write_record(record)

    def test_load_record_rejects_invalid_state(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        job_id = "0123456789ab"
        record_file = mgr.records / f"{job_id}.json"

        for bad_state in ["unknown_state", "hacked", "eval('1')", ""]:
            record = {
                "job_id": job_id,
                "session_id": "s1",
                "session_root": str(tmp_path),
                "state": bad_state,
                "workspace_path": str(tmp_path),
                "updated_at": 1000.0,
            }
            record_file.write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(KeyError):
                mgr._load_record(job_id)

    def test_load_record_rejects_null_byte_in_strings(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        job_id = "0123456789ab"
        record_file = mgr.records / f"{job_id}.json"

        for key in ("workspace_path", "session_root", "session_id", "repository", "branch"):
            record = {
                "job_id": job_id,
                "session_id": "s1",
                "session_root": str(tmp_path),
                "state": "running",
                "workspace_path": str(tmp_path),
                "updated_at": 1000.0,
                key: f"val\0{key}",
            }
            record_file.write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(KeyError):
                mgr._load_record(job_id)


class TestManagerLimitsAndWorkspaceSecurity:
    """Acceptance criteria SF-JOB-01-A3: Limit bounds, root validation, and workspace guard defense."""

    def test_coding_job_manager_rejects_extreme_or_invalid_limits(self, tmp_path: Path):
        for invalid in (101, 1000, 10**6):
            with pytest.raises(ValueError, match="[Ll]imit|[Bb]ound"):
                CodingJobManager(max_active=invalid, root=tmp_path / "jobs")

        for invalid in (1001, 10000, 10**6):
            with pytest.raises(ValueError, match="[Ll]imit|[Bb]ound"):
                CodingJobManager(max_history=invalid, root=tmp_path / "jobs")

    def test_coding_job_manager_rejects_invalid_root_type_or_null_byte(self, tmp_path: Path):
        for invalid in ("not_a_path", 12345, ["jobs"]):
            with pytest.raises(TypeError, match="[Pp]ath"):
                CodingJobManager(root=invalid)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="null byte"):
            CodingJobManager(root=Path("jobs\0bad"))

    def test_active_workspace_rejects_non_path_or_null_byte(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in ["workspace", 12345, None, []]:
            with pytest.raises(TypeError, match="[Pp]ath"):
                mgr.active_workspace(invalid)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="null byte"):
            mgr.active_workspace(Path("work\0space"))

    def test_workspace_guard_rejects_non_path_or_null_byte(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in ["workspace", 12345, None, []]:
            with pytest.raises(TypeError, match="[Pp]ath"), mgr.workspace_guard(invalid):  # type: ignore[arg-type]
                pass

        with pytest.raises(ValueError, match="null byte"), mgr.workspace_guard(Path("work\0space")):
            pass


class TestJobLifecycleSecurity:
    """Acceptance criteria SF-JOB-01-A4: Job lifecycle parameters, symlink rejection, and API defense."""

    def test_start_rejects_invalid_agent_or_task(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        valid_agent = _make_dummy_agent(workspace, tmp_path / "sessions")

        # Invalid agent
        for bad_agent in [None, "not_an_agent", 123, SimpleNamespace()]:
            with pytest.raises(TypeError, match="[Aa]gent"):
                mgr.start(bad_agent, "Do task")  # type: ignore[arg-type]

        # Invalid task
        for bad_task in [None, 123, []]:
            with pytest.raises(TypeError, match="[Tt]ask"):
                mgr.start(valid_agent, bad_task)  # type: ignore[arg-type]

        for bad_task in ["   ", ""]:
            with pytest.raises(ValueError, match="[Tt]ask"):
                mgr.start(valid_agent, bad_task)

        with pytest.raises(ValueError, match="null byte"):
            mgr.start(valid_agent, "task with \0 null byte")

    def test_start_rejects_symlinked_workspace_root(self, tmp_path: Path):
        real_ws = tmp_path / "real_ws"
        real_ws.mkdir()
        sym_ws = tmp_path / "sym_ws"
        sym_ws.symlink_to(real_ws, target_is_directory=True)

        mgr = CodingJobManager(root=tmp_path / "jobs")
        agent = _make_dummy_agent(sym_ws, tmp_path / "sessions")
        with pytest.raises(ValueError, match="[Ss]ymlink"):
            mgr.start(agent, "Do task")

    def test_get_and_cancel_reject_non_string_job_id(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in [None, 12345, [], {}, "bad_id"]:
            with pytest.raises(KeyError):
                mgr.get(invalid)  # type: ignore[arg-type]

            with pytest.raises(KeyError):
                mgr.cancel(invalid)  # type: ignore[arg-type]

    def test_list_jobs_rejects_non_path_repository(self, tmp_path: Path):
        mgr = CodingJobManager(root=tmp_path / "jobs")
        for invalid in ["not_a_path", 12345, []]:
            with pytest.raises(TypeError, match="[Pp]ath"):
                mgr.list_jobs(repository=invalid)  # type: ignore[arg-type]
