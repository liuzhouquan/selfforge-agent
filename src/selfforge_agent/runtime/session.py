"""Append-only local sessions and metadata-only execution traces."""

import fcntl
import io
import json
import math
import os
import re
import stat
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

MAX_REGRESSION_BYTES = 4 * 1024 * 1024
MAX_REGRESSION_LINE_BYTES = 16 * 1024
MAX_REGRESSION_EVENTS = 10000


class RegressionJournalCorruptError(ValueError):
    """The last committed journal entry cannot be decoded as a JSON object."""


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

    def record_verification(self, passed: bool, *, workspace_sha256: str | None = None) -> None:
        if passed and workspace_sha256 is None:
            raise ValueError("Successful verification needs a workspace fingerprint")
        if workspace_sha256 is not None and (
            not isinstance(workspace_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", workspace_sha256)
        ):
            raise ValueError("Invalid workspace fingerprint")
        self._append(self.path, {"type": "verification", "passed": passed,
                                 "evidence": "junit-workspace-v2",
                                 "workspace_sha256": workspace_sha256 if passed else None})

    @staticmethod
    def _environment(value: object) -> dict:
        if (not isinstance(value, dict) or set(value) != {"version", "sandbox", "image_id"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["sandbox"] not in ("docker", "host")
                or not isinstance(value["image_id"], str)):
            raise ValueError("Invalid session execution environment")
        if ((value["sandbox"] == "docker" and not re.fullmatch(
                r"sha256:[0-9a-f]{64}", value["image_id"]))
                or (value["sandbox"] == "host" and value["image_id"])):
            raise ValueError("Invalid session execution environment")
        return value

    def environment(self) -> dict | None:
        if not self.path.is_file():
            return None
        saved = None
        for event in self._complete_events(self.path):
            if event.get("type") == "execution_environment":
                value = self._environment(event.get("environment"))
                if saved is not None and value != saved:
                    raise ValueError("Conflicting session execution environments")
                saved = value
        return saved

    def record_environment(self, environment: dict) -> None:
        environment = self._environment(environment)
        saved = self.environment()
        if saved is not None:
            if saved != environment:
                raise ValueError("Cannot change a session execution environment")
            return
        self._append(self.path, {"type": "execution_environment", "environment": environment})
        self.trace("execution_environment", **environment)

    def verification_passed(self) -> bool:
        return self.verification_snapshot() is not None

    def verification_snapshot(self) -> str | None:
        if not self.path.is_file():
            return None
        snapshot = None
        for event in self._complete_events(self.path):
            if event.get("type") == "verification":
                candidate = event.get("workspace_sha256")
                snapshot = (candidate if event.get("passed") is True
                            and event.get("evidence") == "junit-workspace-v2"
                            and isinstance(candidate, str)
                            and re.fullmatch(r"[0-9a-f]{64}", candidate) else None)
        return snapshot

    def _regression_journal(self, limit: int | None = None) -> tuple[list[dict], dict | None, bool]:
        """Read bounded, committed and sanitized history; uncertainty revokes latest evidence."""
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Limit must be a positive integer")
        path = self.path.with_name(f"{self.session_id}.regression.jsonl")
        fd = None
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                return [], None, False
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REGRESSION_BYTES:
                return [], None, False
            chunks = []
            total = 0
            while total <= MAX_REGRESSION_BYTES:
                chunk = os.read(fd, min(65536, MAX_REGRESSION_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.stat(path, follow_symlinks=False)
            if (total > MAX_REGRESSION_BYTES or not stat.S_ISREG(after.st_mode)
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                return [], None, False
        except OSError:
            return [], None, False
        finally:
            if fd is not None:
                os.close(fd)
        records: deque[dict] = deque(maxlen=min(limit or MAX_REGRESSION_EVENTS, MAX_REGRESSION_EVENTS))
        latest = None
        corrupt = False
        for index, line in enumerate(io.BytesIO(b"".join(chunks))):
            if index >= MAX_REGRESSION_EVENTS:
                return [], None, False
            if not line.endswith(b"\n") or len(line) > MAX_REGRESSION_LINE_BYTES:
                return list(records), None, False
            try:
                event = json.loads(line)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                latest, corrupt = None, True
                continue
            corrupt = not isinstance(event, dict)
            try:
                latest = self._sanitize_regression(event)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                latest = None
            if latest is not None:
                records.append(latest)
        return list(records), latest, corrupt

    def _sanitize_regression(self, event: object) -> dict | None:
        # Imported lazily because the validation service itself uses SessionStore.
        from selfforge_agent.coding.regression_validation import RegressionValidationReport

        if not isinstance(event, dict):
            return None
        report = RegressionValidationReport.model_validate(event, strict=True)
        if (report.session_id != self.session_id
                or not re.fullmatch(r"[0-9a-f]{40}", report.base_sha)
                or not re.fullmatch(r"[0-9a-f]{64}", report.test_sha256)
                or not (re.fullmatch(r"[0-9a-f]{64}", report.workspace_sha256)
                        or (report.status == "error" and report.workspace_sha256 == ""))):
            return None
        for summary in (report.base_result, report.repaired_result):
            if summary is not None and (
                min(summary.passed, summary.failed, summary.errors, summary.skipped) < 0
                or not math.isfinite(summary.duration_seconds) or summary.duration_seconds < 0
            ):
                return None
        if report.status == "confirmed":
            if report.reason != "base_failed_repaired_passed":
                return None
            # Legacy metadata lacks summaries. Preserve its explicit historical status;
            # new records must retain the complete pair, never invent missing counts.
            if (report.base_result is None) != (report.repaired_result is None):
                return None
            if report.base_result is None:
                if event.get("base_status") != "failed" or event.get("repaired_status") != "passed":
                    return None
            else:
                base, repaired = report.base_result, report.repaired_result
                if (base.status != "failed" or base.exit_code != 1 or base.failed <= 0 or base.errors
                        or repaired.status != "passed" or repaired.exit_code != 0
                        or repaired.passed <= 0 or repaired.failed or repaired.errors or repaired.skipped):
                    return None
        result = report.model_dump()
        for name in ("at", "timestamp"):
            if isinstance(event.get(name), str):
                try:
                    result[name] = datetime.fromisoformat(event[name]).isoformat()
                except ValueError:
                    pass
        return result

    def regression_records(self, limit: int | None = None) -> list[dict]:
        return self._regression_journal(limit)[0]

    def latest_regression_record(self, *, strict: bool = False) -> dict | None:
        _, latest, corrupt = self._regression_journal(limit=1)
        if strict and corrupt:
            raise RegressionJournalCorruptError("Corrupted regression validation record")
        return latest

    def confirmed_regression_snapshot(self, *, base_sha: str | None = None) -> str | None:
        record = self.latest_regression_record()
        if (record is None or record["status"] != "confirmed"
                or (base_sha is not None and record["base_sha"] != base_sha)):
            return None
        return record["workspace_sha256"]

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

    def token_metrics(self) -> dict:
        from selfforge_agent.providers import TokenUsage
        from selfforge_agent.runtime.pricing import estimate_monetary_cost, sum_monetary_costs

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        models: set[str] = set()
        providers: set[str] = set()
        costs = []
        complete = True
        events = self._complete_events(self.trace_path) if self.trace_path.exists() else []
        for event in events:
            if event.get("event") == "model_call":
                model = str(event.get("model") or "")
                provider = str(event.get("provider") or "")
                models.add(model)
                providers.add(provider)
                try:
                    usage = TokenUsage.model_validate(event.get("usage"))
                    if min(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) < 0:
                        raise ValueError("Negative usage")
                except ValueError:
                    complete = False
                    costs.append(None)
                    continue
                prompt_tokens += usage.prompt_tokens
                completion_tokens += usage.completion_tokens
                total_tokens += usage.total_tokens
                costs.append(estimate_monetary_cost(model, usage, provider=provider))
        cost = sum_monetary_costs(costs)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "model": next(iter(models)) if len(models) == 1 else None,
            "models": sorted(models),
            "providers": sorted(providers),
            "usage_complete": complete,
            "cost": cost.model_dump() if cost is not None else None,
        }
