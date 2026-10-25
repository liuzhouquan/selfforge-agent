"""Process-safe Web job recovery and cancellation."""

import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.agent import AgentResult
from selfforge_agent.runtime.session import SessionStore


class WaitingAgent:
    def __init__(self, workspace: Path, sessions: Path, started: Event) -> None:
        self.tools = SimpleNamespace(root=workspace)
        self.session = SessionStore(sessions)
        self.cancel = Event()
        self.started = started

    def run(self, _task: str, *, resume: bool = False) -> AgentResult:
        assert not resume
        self.started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.cancel.is_set():
            time.sleep(0.02)
        return AgentResult(self.session.session_id, "cancelled" if self.cancel.is_set()
                           else "timed_out", "", 1, 0)


def _finished(manager: CodingJobManager, job_id: str) -> dict:
    for _ in range(100):
        record = manager.get(job_id)
        if record["state"] not in {"queued", "running"}:
            return record
        time.sleep(0.02)
    pytest.fail("Job did not finish")


def test_two_managers_share_workspace_lock_history_and_cancel(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "jobs"
    first = CodingJobManager(root=root)
    second = CodingJobManager(root=root)
    started = Event()
    agent = WaitingAgent(workspace, tmp_path / "sessions", started)
    job = first.start(agent, "Wait")
    assert started.wait(2)
    assert second.get(job.job_id)["state"] == "running"
    assert second.active_workspace(workspace)
    with pytest.raises(ValueError, match="already running"):
        second.start(WaitingAgent(workspace, tmp_path / "other", Event()), "Conflict")
    with pytest.raises(ValueError, match="still running"), second.workspace_guard(workspace):
        pass

    requested = second.cancel(job.job_id)
    assert requested["cancel_requested"]
    done = _finished(first, job.job_id)
    assert done["state"] == "cancelled"
    assert CodingJobManager(root=root).get(job.job_id)["state"] == "cancelled"
    for _ in range(100):
        if not second.active_workspace(workspace):
            break
        time.sleep(0.01)
    assert not second.active_workspace(workspace)
    assert (root / "records" / f"{job.job_id}.json").stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(api_module, "_CODING_JOBS", second)
    response = TestClient(api_module.app).get("/api/code/jobs",
                                              params={"repository": str(workspace)})
    assert response.status_code == 200
    assert response.json()[0]["job_id"] == job.job_id


def test_crashed_process_job_becomes_interrupted_and_releases_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "jobs"
    script = r'''
import os, sys, time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.session import SessionStore

workspace, root = map(Path, sys.argv[1:])
class Agent:
    def __init__(self):
        self.tools = SimpleNamespace(root=workspace)
        self.session = SessionStore(root / "sessions")
        self.cancel = Event()
    def run(self, task, *, resume=False):
        self.session.record_message({"role": "system", "content": "coding"})
        self.session.record_message({"role": "user", "content": task})
        self.session.record_status("running")
        time.sleep(30)

manager = CodingJobManager(root=root)
job = manager.start(Agent(), "Continue after restart")
for _ in range(100):
    if job.session.path.exists() and manager.get(job.job_id)["state"] == "running":
        break
    time.sleep(0.01)
print(job.job_id, flush=True)
os._exit(0)
'''
    process = subprocess.run([sys.executable, "-c", script, str(workspace), str(root)],
                             capture_output=True, text=True, timeout=10, check=True)
    job_id = process.stdout.strip()
    assert len(job_id) == 12
    manager = CodingJobManager(root=root)
    recovered = manager.get(job_id)
    assert recovered["state"] == "interrupted"
    assert "resume" in recovered["error"]
    assert recovered["can_resume"]
    assert not manager.active_workspace(workspace)
    assert SessionStore(root / "sessions", recovered["session_id"]).load()[0][-1]["role"] == "user"
    with manager.workspace_guard(workspace):
        assert manager.active_workspace(workspace)
    assert manager.get(job_id)["state"] == "interrupted"


def test_crash_before_session_start_reports_new_task_required(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "jobs"
    script = r'''
import os, sys, time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.session import SessionStore

workspace, root = map(Path, sys.argv[1:])
class Agent:
    def __init__(self):
        self.tools = SimpleNamespace(root=workspace)
        self.session = SessionStore(root / "sessions")
        self.cancel = Event()
    def run(self, task, *, resume=False):
        time.sleep(30)

manager = CodingJobManager(root=root)
job = manager.start(Agent(), "Never saved")
print(job.job_id, flush=True)
os._exit(0)
'''
    process = subprocess.run([sys.executable, "-c", script, str(workspace), str(root)],
                             capture_output=True, text=True, timeout=10, check=True)
    manager = CodingJobManager(root=root)
    recovered = manager.get(process.stdout.strip())
    assert recovered["state"] == "failed"
    assert not recovered["can_resume"]
    assert "start a new task" in recovered["error"]
