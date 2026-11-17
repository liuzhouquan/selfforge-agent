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

MAX_SESSION_BYTES = 16 * 1024 * 1024
MAX_SESSION_LINE_BYTES = 1024 * 1024
MAX_SESSION_ID_LENGTH = 64


def _directory_fd(path: Path, *, create: bool = False) -> int:
    """Open every directory component without following aliases, including parents."""
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _state_open(path: Path, flags: int, mode: int = 0o600) -> int:
    directory = _directory_fd(path.parent)
    try:
        return os.open(path.name, flags, mode, dir_fd=directory)
    finally:
        os.close(directory)


class RegressionJournalCorruptError(ValueError):
    """The last committed journal entry cannot be decoded as a JSON object."""


class SessionStore:
    def __init__(self, root: Path, session_id: str | None = None) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path instance")
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("Session root and ancestors must not be symlinks")
        self.root = root.absolute()
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("Session root is not a directory")

        if session_id is not None:
            if not isinstance(session_id, str):
                raise TypeError("session_id must be a string")
            if (
                not session_id
                or not session_id.isalnum()
                or len(session_id) > MAX_SESSION_ID_LENGTH
            ):
                raise ValueError(
                    f"Invalid session ID: must be non-empty alphanumeric up to {MAX_SESSION_ID_LENGTH} chars"
                )
            self.session_id = session_id
        else:
            self.session_id = uuid4().hex[:12]

        directory = self.root / ".selfforge" / "sessions"
        if any(part.is_symlink() for part in (directory, *directory.parents)) or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"Session directory path is invalid or a symlink: {directory}")
        try:
            directory_fd = _directory_fd(directory, create=True)
            os.close(directory_fd)
        except OSError as exc:
            raise ValueError(f"Failed to create session directory: {exc}") from exc
        self.path = directory / f"{self.session_id}.jsonl"
        self.trace_path = directory / f"{self.session_id}.trace.jsonl"

    @staticmethod
    def _append(path: Path, event: dict) -> None:
        if not isinstance(event, dict):
            raise TypeError("event must be a dict")
        try:
            serialized = json.dumps(
                {"at": datetime.now(UTC).isoformat(), **event},
                ensure_ascii=False,
            ) + "\n"
            payload = serialized.encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"Event is not JSON-serializable: {exc}") from exc

        if len(payload) > MAX_SESSION_LINE_BYTES:
            raise ValueError(f"Event payload exceeds maximum line bytes of {MAX_SESSION_LINE_BYTES}")

        if path.is_symlink() or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Cannot append to symlinked path: {path}")

        try:
            fd = _state_open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise OSError(f"Failed to open session file: {exc}") from exc

        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"Target path is not a regular file: {path}")
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
            end = os.lseek(fd, 0, os.SEEK_END)
            if end + len(payload) > MAX_SESSION_BYTES:
                raise ValueError(f"Session file exceeds maximum size of {MAX_SESSION_BYTES} bytes")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("Session write made no progress")
                view = view[written:]
        finally:
            os.close(fd)

    @staticmethod
    def _complete_events(path: Path) -> Iterator[dict]:
        if not path.exists():
            return
        if path.is_symlink() or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Cannot read symlinked session file: {path}")
        fd = None
        try:
            fd = _state_open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"Session path is not a regular file: {path}")
            if st.st_size > MAX_SESSION_BYTES:
                raise ValueError(f"Session file exceeds maximum size of {MAX_SESSION_BYTES} bytes")
            fcntl.flock(fd, fcntl.LOCK_SH)
            chunks = []
            total = 0
            while total <= MAX_SESSION_BYTES:
                chunk = os.read(fd, min(65536, MAX_SESSION_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > MAX_SESSION_BYTES:
                raise ValueError(f"Session file exceeds maximum size of {MAX_SESSION_BYTES} bytes")
        finally:
            if fd is not None:
                os.close(fd)

        for line in io.BytesIO(b"".join(chunks)):
            if not line.endswith(b"\n"):
                break  # A writer may still be finishing the last event.
            if len(line) > MAX_SESSION_LINE_BYTES:
                raise ValueError(f"Session line exceeds maximum size of {MAX_SESSION_LINE_BYTES} bytes")
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped)
            if not isinstance(data, dict):
                raise TypeError(f"Session event must be a JSON object, got {type(data).__name__}")
            yield data

    def record_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a dict")
        self._append(self.path, {"type": "message", "message": message})

    def record_compaction(self, messages: list[dict]) -> None:
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise TypeError("messages must be a list of dicts")
        self._append(self.path, {"type": "compaction", "messages": messages})
        self.trace("context_compaction", messages=len(messages))

    def record_status(self, status: str, *, detail: str = "") -> None:
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be a non-empty string")
        if not isinstance(detail, str):
            raise TypeError("detail must be a string")
        self._append(self.path, {"type": "status", "status": status.strip(), "detail": detail})
        self.trace("status", status=status.strip(), detail=detail[:300])

    def record_verification(self, passed: bool, *, workspace_sha256: str | None = None) -> None:
        if type(passed) is not bool:
            raise TypeError("passed must be a boolean")
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
            fd = _state_open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
        if not isinstance(event, str) or not event.strip():
            return
        try:
            self._append(self.trace_path, {"event": event.strip(), **fields})
        except (OSError, ValueError, TypeError):
            pass

    def load(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            raise ValueError(f"Unknown session: {self.session_id}")
        messages: list[dict] = []
        status = None
        for event in self._complete_events(self.path):
            if not isinstance(event, dict):
                continue
            ev_type = event.get("type")
            if ev_type == "message":
                msg = event.get("message")
                if isinstance(msg, dict):
                    messages.append(msg)
            elif ev_type == "compaction":
                msgs = event.get("messages")
                if isinstance(msgs, list):
                    messages = [m for m in msgs if isinstance(m, dict)]
            elif ev_type == "status":
                s = event.get("status")
                if isinstance(s, str) and s.strip():
                    status = s.strip()
        return messages, status

    def trace_events(self, limit: int | None = None) -> list[dict]:
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Trace limit must be positive integer")
        if not self.trace_path.exists():
            return []
        events: deque[dict] = deque(maxlen=limit)
        for event in self._complete_events(self.trace_path):
            if isinstance(event, dict):
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
            if not isinstance(event, dict):
                continue
            if event.get("event") == "model_call":
                model = str(event.get("model") or "")
                provider = str(event.get("provider") or "")
                if model:
                    models.add(model)
                if provider:
                    providers.add(provider)
                raw_usage = event.get("usage")
                if not isinstance(raw_usage, (dict, TokenUsage)):
                    complete = False
                    costs.append(None)
                    continue
                try:
                    usage = (
                        raw_usage
                        if isinstance(raw_usage, TokenUsage)
                        else TokenUsage.model_validate(raw_usage)
                    )
                    if min(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) < 0:
                        raise ValueError("Negative usage")
                    if not (
                        math.isfinite(usage.prompt_tokens)
                        and math.isfinite(usage.completion_tokens)
                        and math.isfinite(usage.total_tokens)
                    ):
                        raise ValueError("Non-finite usage")
                except (ValueError, TypeError):
                    complete = False
                    costs.append(None)
                    continue
                prompt_tokens += usage.prompt_tokens
                completion_tokens += usage.completion_tokens
                total_tokens += usage.total_tokens
                try:
                    c = estimate_monetary_cost(model, usage, provider=provider)
                    costs.append(c)
                except (ValueError, TypeError, KeyError, AttributeError):
                    costs.append(None)
        try:
            cost = sum_monetary_costs(costs)
        except (ValueError, TypeError, KeyError, AttributeError):
            cost = None
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
            "model": next(iter(models)) if len(models) == 1 else None,
            "models": sorted(models),
            "providers": sorted(providers),
            "usage_complete": complete,
            "cost": cost.model_dump() if cost is not None else None,
        }
