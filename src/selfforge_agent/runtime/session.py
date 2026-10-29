"""Append-only local sessions and metadata-only execution traces."""

import fcntl
import json
import os
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class SessionStore:
    def __init__(self, root: Path, session_id: str | None = None) -> None:
        self.root = root.resolve()
        self.session_id = session_id or uuid4().hex[:12]
        if not self.session_id.isalnum():
            raise ValueError("Invalid session ID")
        directory = self.root / ".selfforge" / "sessions"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / f"{self.session_id}.jsonl"
        self.trace_path = directory / f"{self.session_id}.trace.jsonl"

    @staticmethod
    def _append(path: Path, event: dict) -> None:
        payload = (json.dumps({"at": datetime.now(UTC).isoformat(), **event},
                              ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            end = os.lseek(fd, 0, os.SEEK_END)
            if end and os.pread(fd, 1, end - 1) != b"\n":
                # A killed writer may leave an uncommitted final record. Repair it
                # while holding the writer lock, before any new event is appended.
                position = end
                while position:
                    start = max(0, position - 8192)
                    newline = os.pread(fd, position - start, start).rfind(b"\n")
                    if newline >= 0:
                        os.ftruncate(fd, start + newline + 1)
                        break
                    position = start
                else:
                    os.ftruncate(fd, 0)
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)

    @staticmethod
    def _complete_events(path: Path) -> Iterator[dict]:
        with path.open("rb") as handle:
            for line in handle:
                if not line.endswith(b"\n"):
                    break  # A writer may still be finishing the last event.
                yield json.loads(line)

    def record_message(self, message: dict) -> None:
        self._append(self.path, {"type": "message", "message": message})

    def record_compaction(self, messages: list[dict]) -> None:
        self._append(self.path, {"type": "compaction", "messages": messages})
        self.trace("context_compaction", messages=len(messages))

    def record_status(self, status: str, *, detail: str = "") -> None:
        self._append(self.path, {"type": "status", "status": status, "detail": detail})
        self.trace("status", status=status, detail=detail[:300])

    def record_verification(self, passed: bool) -> None:
        self._append(self.path, {"type": "verification", "passed": passed,
                                 "evidence": "junit-v1"})

    def verification_passed(self) -> bool:
        if not self.path.is_file():
            return False
        passed = False
        for event in self._complete_events(self.path):
            if event.get("type") == "verification":
                # Legacy success only checked exit=0, which includes all-skipped
                # suites. Keep that history, but require a fresh verified run.
                passed = event["passed"] is True and event.get("evidence") == "junit-v1"
        return passed

    def trace(self, event: str, **fields: object) -> None:
        self._append(self.trace_path, {"event": event, **fields})

    def load(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            raise ValueError(f"Unknown session: {self.session_id}")
        messages: list[dict] = []
        status = None
        for event in self._complete_events(self.path):
            if event.get("type") == "message":
                messages.append(event["message"])
            elif event.get("type") == "compaction":
                messages = event["messages"]
            elif event.get("type") == "status":
                status = event["status"]
        return messages, status

    def trace_events(self, limit: int | None = None) -> list[dict]:
        if limit is not None and limit <= 0:
            raise ValueError("Trace limit must be positive")
        if not self.trace_path.exists():
            return []
        events: deque[dict] = deque(maxlen=limit)
        for event in self._complete_events(self.trace_path):
            events.append(event)
        return list(events)
