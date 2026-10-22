"""Append-only local sessions and metadata-only execution traces."""

import json
import os
from collections import deque
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
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), **event},
                                    ensure_ascii=False) + "\n")

    def record_message(self, message: dict) -> None:
        self._append(self.path, {"type": "message", "message": message})

    def record_compaction(self, messages: list[dict]) -> None:
        self._append(self.path, {"type": "compaction", "messages": messages})
        self.trace("context_compaction", messages=len(messages))

    def record_status(self, status: str, *, detail: str = "") -> None:
        self._append(self.path, {"type": "status", "status": status, "detail": detail})
        self.trace("status", status=status, detail=detail[:300])

    def trace(self, event: str, **fields: object) -> None:
        self._append(self.trace_path, {"event": event, **fields})

    def load(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            raise ValueError(f"Unknown session: {self.session_id}")
        messages: list[dict] = []
        status = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
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
        with self.trace_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.endswith("\n"):
                    break  # A concurrent writer may still be finishing this event.
                events.append(json.loads(line))
        return list(events)
