"""Bounded background jobs for the local Coding Agent workbench."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from selfforge_agent.runtime.agent import AgentResult, CodingAgent
from selfforge_agent.runtime.session import SessionStore


@dataclass
class CodingJob:
    job_id: str
    workspace: Path
    session: SessionStore
    cancel: Event
    state: str = "queued"
    result: AgentResult | None = None
    error: str = ""


class CodingJobManager:
    def __init__(self, max_active: int = 2, max_history: int = 50) -> None:
        if max_active <= 0 or max_history <= 0:
            raise ValueError("Positive job limits are required")
        self.max_active = max_active
        self.max_history = max_history
        self._jobs: dict[str, CodingJob] = {}
        self._lock = Lock()

    def start(self, agent: CodingAgent, task: str, *, resume: bool = False) -> CodingJob:
        with self._lock:
            active = [job for job in self._jobs.values() if job.state in {"queued", "running"}]
            if len(active) >= self.max_active:
                raise ValueError("Too many coding tasks are running")
            if any(job.workspace == agent.tools.root for job in active):
                raise ValueError("A coding task is already running in this workspace")
            finished = [key for key, job in self._jobs.items()
                        if job.state not in {"queued", "running"}]
            for key in finished[:max(0, len(self._jobs) - self.max_history + 1)]:
                del self._jobs[key]
            job = CodingJob(uuid4().hex[:12], agent.tools.root, agent.session, agent.cancel)
            self._jobs[job.job_id] = job

        def work() -> None:
            with self._lock:
                job.state = "running"
            try:
                result = agent.run(task, resume=resume)
            except Exception as exc:  # noqa: BLE001 - worker boundary must always finalize the job
                # Unexpected exceptions must not leave a job stuck in "running".
                try:
                    agent.session.record_status("failed")
                except OSError:
                    pass
                with self._lock:
                    job.state = "failed"
                    job.error = f"Unexpected {type(exc).__name__}"
            else:
                with self._lock:
                    job.result = result
                    job.state = result.status

        try:
            Thread(target=work, name=f"selfforge-{job.job_id}", daemon=True).start()
        except RuntimeError:
            with self._lock:
                del self._jobs[job.job_id]
            raise
        return job

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            result = job.result
            state = job.state
            error = job.error
            cancelled = job.cancel.is_set()
            session = job.session
        return {
            "job_id": job_id, "session_id": session.session_id, "state": state,
            "answer": result.answer if result else "",
            "steps": result.steps if result else 0,
            "tools_used": result.tools_used if result else 0,
            "error": result.error if result else error,
            "cancel_requested": cancelled,
            "trace": session.trace_events(limit=30),
        }

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.state in {"queued", "running"}:
                job.cancel.set()
        return self.get(job_id)
