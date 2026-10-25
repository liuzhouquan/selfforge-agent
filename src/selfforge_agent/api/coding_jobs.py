"""Durable local Web jobs with process-safe workspace ownership."""

import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from selfforge_agent.runtime.agent import AgentResult, CodingAgent
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace

_JOB_ID = re.compile(r"[0-9a-f]{12}\Z")
_ACTIVE = {"queued", "running"}


class DurableCancel(Event):
    """Local event backed by a marker other server processes can create."""

    def __init__(self, marker: Path, previous: Event) -> None:
        super().__init__()
        self.marker = marker
        if previous.is_set():
            self.set()

    def is_set(self) -> bool:
        return super().is_set() or self.marker.is_file()

    def set(self) -> None:
        fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        super().set()


@dataclass
class CodingJob:
    job_id: str
    workspace: Path
    session: SessionStore
    cancel: DurableCancel
    repository: Path
    branch: str = ""
    base_sha: str = ""
    state: str = "queued"
    result: AgentResult | None = None
    error: str = ""
    lock_fd: int = field(default=-1, repr=False)


class CodingJobManager:
    def __init__(self, max_active: int = 2, max_history: int = 50,
                 root: Path | None = None) -> None:
        if max_active <= 0 or max_history <= 0:
            raise ValueError("Positive job limits are required")
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        self.root = (root or data_home / "selfforge-agent" / "jobs").expanduser().resolve()
        self.records = self.root / "records"
        self.locks = self.root / "locks"
        self.cancellations = self.root / "cancel"
        for directory in (self.records, self.locks, self.cancellations):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_active = max_active
        self.max_history = max_history
        self._jobs: dict[str, CodingJob] = {}
        self._lock = Lock()

    def _record_path(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise KeyError(job_id)
        return self.records / f"{job_id}.json"

    def _cancel_path(self, job_id: str) -> Path:
        self._record_path(job_id)
        return self.cancellations / job_id

    def _lock_path(self, workspace: Path) -> Path:
        digest = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()
        return self.locks / digest

    def _try_workspace_lock(self, workspace: Path) -> int | None:
        fd = os.open(self._lock_path(workspace), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    @contextmanager
    def workspace_guard(self, workspace: Path):
        """Hold the same lock used by active jobs while removing a worktree."""
        fd = self._try_workspace_lock(workspace)
        if fd is None:
            raise ValueError("Coding task is still running in this workspace")
        try:
            yield
        finally:
            self._unlock(fd)

    def _write_record(self, record: dict) -> None:
        target = self._record_path(record["job_id"])
        temporary = self.records / f".{record['job_id']}.{uuid4().hex}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _record(job: CodingJob) -> dict:
        result = job.result
        return {
            "job_id": job.job_id,
            "session_id": job.session.session_id,
            "session_root": str(job.session.root),
            "state": job.state,
            "answer": result.answer if result else "",
            "steps": result.steps if result else 0,
            "tools_used": result.tools_used if result else 0,
            "error": result.error if result else job.error,
            "repository": str(job.repository),
            "workspace_path": str(job.workspace),
            "branch": job.branch,
            "base_sha": job.base_sha,
            "updated_at": time.time(),
        }

    def _load_record(self, job_id: str) -> dict:
        try:
            record = json.loads(self._record_path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError(job_id) from exc
        if (not isinstance(record, dict) or record.get("job_id") != job_id
                or not isinstance(record.get("workspace_path"), str)
                or not isinstance(record.get("session_root"), str)
                or not isinstance(record.get("session_id"), str)):
            raise KeyError(job_id)
        return record

    def _recover(self, record: dict) -> dict:
        if record.get("state") not in _ACTIVE:
            return record
        fd = self._try_workspace_lock(Path(record["workspace_path"]))
        if fd is None:
            return record
        try:
            # The former process lost its lock. Keep its session for an explicit resume.
            current = self._load_record(record["job_id"])
            if current.get("state") in _ACTIVE:
                session = SessionStore(Path(current["session_root"]), current["session_id"])
                resumable = session.path.is_file() and Path(current["workspace_path"]).is_dir()
                current["state"] = "interrupted" if resumable else "failed"
                current["error"] = (
                    "Server stopped while this task was running; resume the session"
                    if resumable else
                    "Server stopped before a resumable session was saved; start a new task"
                )
                current["updated_at"] = time.time()
                self._write_record(current)
            return current
        finally:
            self._unlock(fd)

    def start(self, agent: CodingAgent, task: str, *, resume: bool = False,
              workspace_info: CodingWorkspace | None = None) -> CodingJob:
        with self._lock:
            active = [job for job in self._jobs.values() if job.state in _ACTIVE]
            if len(active) >= self.max_active:
                raise ValueError("Too many coding tasks are running")
            fd = self._try_workspace_lock(agent.tools.root)
            if fd is None:
                raise ValueError("A coding task is already running in this workspace")
            try:
                if not agent.tools.root.is_dir():
                    raise ValueError("Coding workspace no longer exists")
                job_id = uuid4().hex[:12]
                while self._record_path(job_id).exists():
                    job_id = uuid4().hex[:12]
                cancel = DurableCancel(self._cancel_path(job_id), agent.cancel)
                agent.cancel = cancel
                job = CodingJob(job_id, agent.tools.root, agent.session, cancel,
                                workspace_info.repository if workspace_info else agent.tools.root,
                                workspace_info.branch if workspace_info else "",
                                workspace_info.base_sha if workspace_info else "",
                                lock_fd=fd)
                self._write_record(self._record(job))
                self._jobs[job_id] = job
                finished = [key for key, item in self._jobs.items() if item.state not in _ACTIVE]
                for key in finished[:max(0, len(self._jobs) - self.max_history)]:
                    del self._jobs[key]
            except (OSError, ValueError):
                self._unlock(fd)
                raise

        def work() -> None:
            try:
                with self._lock:
                    job.state = "running"
                    self._write_record(self._record(job))
                result = agent.run(task, resume=resume)
                with self._lock:
                    job.result = result
                    job.state = result.status
                    self._write_record(self._record(job))
            except Exception as exc:  # noqa: BLE001 - worker boundary must always finalize the job
                try:
                    agent.session.record_status("failed")
                except OSError:
                    pass
                with self._lock:
                    job.state = "failed"
                    job.error = f"Unexpected {type(exc).__name__}"
                    try:
                        self._write_record(self._record(job))
                    except OSError:
                        pass
            finally:
                self._unlock(job.lock_fd)

        try:
            Thread(target=work, name=f"selfforge-{job.job_id}", daemon=True).start()
        except RuntimeError:
            with self._lock:
                del self._jobs[job.job_id]
                self._record_path(job.job_id).unlink(missing_ok=True)
            self._unlock(job.lock_fd)
            raise
        return job

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            record = self._record(job) if job else self._load_record(job_id)
        if job is None:
            record = self._recover(record)
        session = job.session if job else SessionStore(Path(record["session_root"]),
                                                        record["session_id"])
        return {
            "job_id": job_id, "session_id": record["session_id"],
            "state": record["state"], "answer": record["answer"],
            "steps": record["steps"], "tools_used": record["tools_used"],
            "error": record["error"],
            "cancel_requested": self._cancel_path(job_id).is_file(),
            "repository": record["repository"],
            "workspace_path": record["workspace_path"],
            "branch": record["branch"], "base_sha": record["base_sha"],
            "can_resume": session.path.is_file() and Path(record["workspace_path"]).is_dir(),
            "trace": session.trace_events(limit=30),
        }

    def list_jobs(self, repository: Path | None = None) -> list[dict]:
        target = str(repository.resolve()) if repository else None
        records = sorted(self.records.glob("*.json"), key=lambda path: path.stat().st_mtime,
                         reverse=True)
        jobs = []
        for path in records:
            try:
                item = self.get(path.stem)
            except (KeyError, OSError, ValueError):
                continue
            if target is None or item["repository"] == target:
                jobs.append(item)
            if len(jobs) >= self.max_history:
                break
        return jobs

    def active_workspace(self, path: Path) -> bool:
        fd = self._try_workspace_lock(path)
        if fd is None:
            return True
        self._unlock(fd)
        return False

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            state = job.state if job else self._load_record(job_id).get("state")
            if state in _ACTIVE:
                if job:
                    job.cancel.set()
                else:
                    fd = os.open(self._cancel_path(job_id), os.O_WRONLY | os.O_CREAT, 0o600)
                    os.close(fd)
        return self.get(job_id)
