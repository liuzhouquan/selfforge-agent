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
MAX_TRACE_BYTES = 16 * 1024 * 1024
MAX_SESSION_LINE_BYTES = 1024 * 1024
MAX_SESSION_ID_LENGTH = 64
MAX_SESSION_EVENTS = 50_000
MAX_TRACE_QUERY_LIMIT = 10_000


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


class SessionCorruptError(ValueError):
    """The session file contains unrecoverable or invalid structure."""


_SENSITIVE_FIELD_NAMES = frozenset({
    "api_key",
    "token",
    "secret",
    "password",
    "access_token",
    "auth",
    "authorization",
})


def redact_sensitive_text(text: str, *, root: Path | None = None) -> str:
    if not isinstance(text, str):
        return text
    res = text
    if root is not None:
        try:
            root_str = str(root.resolve())
            if root_str and root_str in res:
                res = res.replace(root_str, "[ROOT]")
        except (OSError, RuntimeError, ValueError):
            pass
    try:
        home_str = str(Path.home().resolve())
        if home_str and len(home_str) > 1 and home_str in res:
            res = res.replace(home_str, "~")
    except (OSError, RuntimeError, ValueError):
        pass

    res = re.sub(r"sk-[a-zA-Z0-9_\-]{20,}", "sk-***", res)
    res = re.sub(r"gh[pousr]_[a-zA-Z0-9]{20,}", lambda m: m.group(0)[:4] + "***", res)
    res = re.sub(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{20,}", r"\1***", res)
    res = re.sub(r"(https?://)([^:\s]+):([^@\s]+)@", r"\1\2:***@", res)
    return res


def redact_event(event: object, *, root: Path | None = None) -> object:
    if isinstance(event, dict):
        clean = {}
        for k, v in event.items():
            if isinstance(k, str) and (
                k.lower() in _SENSITIVE_FIELD_NAMES
                or any(k.lower().endswith(s) for s in ("_key", "_token", "_secret", "_password"))
            ):
                clean[k] = "***"
            else:
                clean[k] = redact_event(v, root=root)
        return clean
    if isinstance(event, list):
        return [redact_event(item, root=root) for item in event]
    if isinstance(event, str):
        return redact_sensitive_text(event, root=root)
    return event


def _atomic_write_file(target_path: Path, content: bytes) -> Path:
    if not isinstance(target_path, Path):
        raise TypeError("target_path must be a Path instance")
    if "\0" in str(target_path):
        raise ValueError("target_path must not contain null bytes")
    if target_path.is_symlink() or any(p.is_symlink() for p in (target_path, *target_path.parents)):
        raise ValueError(f"Cannot export to symlinked path: {target_path}")

    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    if any(p.is_symlink() for p in (target_dir, *target_dir.parents)):
        raise ValueError(f"Cannot export to symlinked directory: {target_dir}")

    tmp_path = target_dir / f".{target_path.name}.tmp.{uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(tmp_path), flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("Atomic write made no progress")
            view = view[written:]
        try:
            os.fdatasync(fd)
        except (AttributeError, OSError):
            try:
                os.fsync(fd)
            except OSError:
                pass
    finally:
        os.close(fd)

    os.replace(tmp_path, target_path)
    return target_path



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
            max_allowed = (
                min(MAX_SESSION_BYTES, MAX_TRACE_BYTES)
                if path.name.endswith(".trace.jsonl")
                else MAX_SESSION_BYTES
            )
            if end + len(payload) > max_allowed:
                raise ValueError(f"Session file exceeds maximum size of {max_allowed} bytes")
            if end:
                event_count = 0
                pos = 0
                while pos < end:
                    chk = os.pread(fd, 65536, pos)
                    if not chk:
                        break
                    event_count += chk.count(b"\n")
                    pos += len(chk)
                if event_count >= MAX_SESSION_EVENTS:
                    raise ValueError(f"Session exceeds maximum event limit of {MAX_SESSION_EVENTS}")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("Session write made no progress")
                view = view[written:]
            try:
                os.fdatasync(fd)
            except (AttributeError, OSError):
                try:
                    os.fsync(fd)
                except OSError:
                    pass
        finally:
            os.close(fd)

    @staticmethod
    def _read_file_chunks(path: Path, max_bytes: int | None = None) -> list[bytes]:
        limit = MAX_SESSION_BYTES if max_bytes is None else max_bytes
        if not path.exists():
            return []
        if path.is_symlink() or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Cannot read symlinked session file: {path}")
        fd = None
        try:
            fd = _state_open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"Session path is not a regular file: {path}")
            if st.st_size > limit:
                raise ValueError(f"Session file exceeds maximum size of {limit} bytes")
            fcntl.flock(fd, fcntl.LOCK_SH)
            chunks = []
            total = 0
            while total <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > limit:
                raise ValueError(f"Session file exceeds maximum size of {limit} bytes")
            return chunks
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _complete_events(path: Path, *, recover: bool = False) -> Iterator[dict]:
        chunks = SessionStore._read_file_chunks(path)
        for line in io.BytesIO(b"".join(chunks)):
            if not line.endswith(b"\n"):
                break  # A writer may still be finishing the last event.
            if len(line) > MAX_SESSION_LINE_BYTES:
                if recover:
                    continue
                raise ValueError(f"Session line exceeds maximum size of {MAX_SESSION_LINE_BYTES} bytes")
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except (ValueError, TypeError, UnicodeError):
                if recover:
                    continue
                raise
            if not isinstance(data, dict):
                if recover:
                    continue
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
            if self.trace_path.exists():
                try:
                    if self.trace_path.stat().st_size >= MAX_TRACE_BYTES:
                        return
                except OSError:
                    pass
            sanitized_fields = redact_event(fields, root=self.root)
            if not isinstance(sanitized_fields, dict):
                sanitized_fields = fields
            self._append(self.trace_path, {"event": event.strip(), **sanitized_fields})
        except (OSError, ValueError, TypeError):
            pass

    def load(self, *, recover: bool = False) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            raise ValueError(f"Unknown session: {self.session_id}")
        messages: list[dict] = []
        status = None
        for event in self._complete_events(self.path, recover=recover):
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

    def recover_session(self) -> tuple[list[dict], str | None, int]:
        if not self.path.is_file():
            raise ValueError(f"Unknown session: {self.session_id}")
        messages: list[dict] = []
        status = None
        corrupt_count = 0
        chunks = self._read_file_chunks(self.path)
        for line in io.BytesIO(b"".join(chunks)):
            if not line.endswith(b"\n"):
                break
            if len(line) > MAX_SESSION_LINE_BYTES:
                corrupt_count += 1
                continue
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
                if not isinstance(data, dict):
                    corrupt_count += 1
                    continue
            except (ValueError, TypeError, UnicodeError):
                corrupt_count += 1
                continue
            ev_type = data.get("type")
            if ev_type == "message":
                msg = data.get("message")
                if isinstance(msg, dict):
                    messages.append(msg)
            elif ev_type == "compaction":
                msgs = data.get("messages")
                if isinstance(msgs, list):
                    messages = [m for m in msgs if isinstance(m, dict)]
            elif ev_type == "status":
                s = data.get("status")
                if isinstance(s, str) and s.strip():
                    status = s.strip()
        return messages, status, corrupt_count

    def trace_events(self, limit: int | None = None) -> list[dict]:
        if limit is not None:
            if type(limit) is not int or limit <= 0:
                raise ValueError("Trace limit must be positive integer")
            if limit > MAX_TRACE_QUERY_LIMIT:
                raise ValueError(f"Trace limit exceeds maximum of {MAX_TRACE_QUERY_LIMIT}")
        if not self.trace_path.exists():
            return []
        events: deque[dict] = deque(maxlen=limit)
        for event in self._complete_events(self.trace_path):
            if isinstance(event, dict):
                events.append(event)
        return list(events)

    def export_snapshot(self, target_path: Path, *, redact: bool = True) -> Path:
        messages, status = self.load(recover=True)
        snapshot = {
            "session_id": self.session_id,
            "status": status,
            "messages": messages,
            "environment": self.environment(),
            "verification_snapshot": self.verification_snapshot(),
            "exported_at": datetime.now(UTC).isoformat(),
        }
        if redact:
            snapshot = redact_event(snapshot, root=self.root)
        serialized = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
        return _atomic_write_file(target_path, serialized.encode("utf-8"))

    def export_trace(self, target_path: Path, *, redact: bool = True) -> Path:
        events = self.trace_events()
        if redact:
            events = [redact_event(ev, root=self.root) for ev in events]
        lines = [json.dumps(ev, ensure_ascii=False) for ev in events]
        content = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        return _atomic_write_file(target_path, content)

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
