"""Explicit, private test-diagnostic snapshots; never model or source archives."""

import json
import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from selfforge_agent.models import TestResult

MAX_ARTIFACT_BYTES = 128 * 1024
MAX_REPEAT_INDEX = 100_000
MAX_REASON_CHARS = 4096
_CASE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}\Z")
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}-[0-9]{1,6}\.json\Z")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_REPEAT_INDEX",
    "FailureArtifact",
    "FailureSummary",
    "cleanup_temporary_artifacts",
    "list_failures",
    "prepare_artifacts",
    "read_failure",
    "summarize_failures",
    "write_failure",
]


class FailureArtifact(BaseModel):
    version: int = Field(default=1)
    case: str = Field(min_length=1, max_length=128)
    repeat: int = Field(ge=0, le=MAX_REPEAT_INDEX)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    visible: dict | None = None
    holdout: dict | None = None

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Only artifact version 1 is supported")
        return value

    @field_validator("case")
    @classmethod
    def _validate_case(cls, value: str) -> str:
        if not _CASE_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid case name: {value!r}")
        return value

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("null byte is not allowed in failure reason")
        if _ANSI_ESCAPE_RE.search(value):
            raise ValueError("ANSI escape sequences are not allowed in failure reason")
        clean = value.replace("\r", "").strip()
        if not clean:
            raise ValueError("failure reason cannot be empty or whitespace")
        return clean

    @field_validator("visible", "holdout")
    @classmethod
    def _validate_summary(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        excluded = {"command", "environment", "stdout", "stderr"}
        allowed = set(TestResult.model_fields) - excluded
        if not isinstance(value, dict) or not set(value) <= allowed:
            raise ValueError("Invalid artifact test summary structure")
        return value


class FailureSummary(BaseModel):
    total_failures: int = Field(ge=0)
    failed_cases: list[str] = Field(default_factory=list)
    reasons: dict[str, int] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


def _validate_reason_text(reason: str) -> str:
    if not isinstance(reason, str):
        raise TypeError("Invalid failure reason: must be string")
    if "\0" in reason:
        raise ValueError("null byte is not allowed in failure reason")
    if _ANSI_ESCAPE_RE.search(reason):
        raise ValueError("ANSI escape sequences are not allowed in failure reason")
    clean = reason.replace("\r", "").strip()
    if not clean or len(clean) > MAX_REASON_CHARS:
        raise ValueError("Invalid failure reason: empty, whitespace, or exceeds 4096 characters")
    return clean


def prepare_artifacts(directory: Path | str, repositories: list[Path | str]) -> Path:
    if not isinstance(directory, (Path, str)) or not str(directory).strip():
        raise ValueError("directory must be a valid Path or non-empty string")
    if "\0" in str(directory):
        raise ValueError("null bytes not allowed in directory path")

    directory_path = Path(directory).expanduser()
    if directory_path.is_symlink():
        raise ValueError("Failure artifacts directory must be a non-symlink")
    parent = directory_path.resolve()

    if not isinstance(repositories, list):
        raise TypeError("repositories must be a list of Path instances")

    for repository in repositories:
        if not isinstance(repository, (Path, str)):
            raise TypeError("each repository must be a Path or str instance")
        if "\0" in str(repository):
            raise ValueError("null bytes not allowed in repository path")
        repo_path = Path(repository).expanduser()
        if repo_path.is_symlink():
            raise ValueError("repository path must not be a symlink")
        repo_resolved = repo_path.resolve()
        if parent == repo_resolved or parent.is_relative_to(repo_resolved):
            raise ValueError("Failure artifacts must stay outside evaluated repositories")
        if repo_resolved.is_relative_to(parent):
            raise ValueError("Evaluated repositories must stay outside failure artifacts directory")

    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = parent / uuid4().hex
    root.mkdir(mode=0o700)
    return root


def _truncate_summary_for_budget(summary: dict | None, max_error_bytes: int = 32 * 1024) -> dict | None:
    if summary is None:
        return None
    summary_copy = dict(summary)
    if summary_copy.get("error_message"):
        msg = str(summary_copy["error_message"])
        if len(msg.encode("utf-8")) > max_error_bytes:
            summary_copy["error_message"] = msg[:max_error_bytes // 2] + "\n... [truncated]"

    details = summary_copy.get("failure_details")
    if details and isinstance(details, list):
        truncated_details = []
        accum = 0
        details_budget = 32 * 1024
        for d in details:
            d_dict = d if isinstance(d, dict) else (d.model_dump() if hasattr(d, "model_dump") else dict(d))
            d_clean = dict(d_dict)
            if "details" in d_clean and len(str(d_clean["details"]).encode("utf-8")) > 1024:
                d_clean["details"] = str(d_clean["details"])[:1024] + "\n... [truncated]"
            d_bytes = len(json.dumps(d_clean, ensure_ascii=False).encode("utf-8"))
            if accum + d_bytes < details_budget:
                truncated_details.append(d_clean)
                accum += d_bytes
            else:
                summary_copy["failure_details_truncated"] = True
                break
        if len(truncated_details) < len(details):
            summary_copy["failure_details_truncated"] = True
        summary_copy["failure_details"] = truncated_details
    return summary_copy


def write_failure(root: Path, *, case: str, repeat: int, reason: str,
                  visible: TestResult | None, holdout: TestResult | None) -> str:
    if not isinstance(case, str) or not _CASE_PATTERN.fullmatch(case):
        raise ValueError(f"Invalid case name: {case!r}")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0 or repeat > MAX_REPEAT_INDEX:
        raise ValueError(f"Invalid repeat index: {repeat!r}")
    clean_reason = _validate_reason_text(reason)

    if visible is not None and not isinstance(visible, TestResult):
        raise TypeError(f"visible must be a TestResult instance or None, got {type(visible).__name__}")
    if holdout is not None and not isinstance(holdout, TestResult):
        raise TypeError(f"holdout must be a TestResult instance or None, got {type(holdout).__name__}")

    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact root must be an existing non-symlink directory")

    filename = f"{case}-{repeat}.json"
    path = root / filename
    if path.is_symlink():
        raise ValueError("Artifact target path cannot be a symlink")
    if path.resolve().parent != root.resolve():
        raise ValueError("Path traversal detected in artifact filename")

    def details(result: TestResult | None) -> dict | None:
        if result is None:
            return None
        return result.model_dump(exclude={"command", "environment", "stdout", "stderr"})

    visible_summary = details(visible)
    holdout_summary = details(holdout)

    data = {
        "version": 1,
        "case": case,
        "repeat": repeat,
        "reason": clean_reason,
        "visible": visible_summary,
        "holdout": holdout_summary,
    }

    # Initial serialization attempt
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        # Graceful truncation of oversized error message or failure details
        data["visible"] = _truncate_summary_for_budget(visible_summary)
        data["holdout"] = _truncate_summary_for_budget(holdout_summary)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_ARTIFACT_BYTES:
            # Further truncate error messages to 2 KiB
            data["visible"] = _truncate_summary_for_budget(data["visible"], max_error_bytes=2048)
            data["holdout"] = _truncate_summary_for_budget(data["holdout"], max_error_bytes=2048)
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > MAX_ARTIFACT_BYTES:
                raise ValueError("Failure artifact exceeds 128 KiB even after truncation")

    _validate_payload(data, filename)

    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{filename}.{uuid4().hex}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # link is atomic and refuses an existing destination, including symlinks.
                os.link(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                        follow_symlinks=False)
            except FileExistsError as exc:
                raise FileExistsError(f"Artifact {filename} already exists in root directory") from exc
        finally:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    return filename


def read_failure(root: Path, filename: str) -> dict:
    if (not isinstance(filename, str)
            or not _FILENAME_PATTERN.fullmatch(filename)
            or filename != Path(filename).name):
        raise ValueError("Invalid failure artifact filename or path traversal detected")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact root must be an existing non-symlink directory")

    path = root / filename
    if path.is_symlink():
        raise ValueError("Artifact file is a symlink")
    if path.resolve().parent != root.resolve():
        raise ValueError("Path traversal detected in artifact filename")

    directory_fd = fd = -1
    try:
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Artifact must be a regular file, not a FIFO or device")
        if info.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact file exceeds maximum allowed size")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            content = handle.read(MAX_ARTIFACT_BYTES + 1)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact file exceeds maximum allowed size")
    except OSError as exc:
        raise ValueError("Failed to read artifact file") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if directory_fd >= 0:
            os.close(directory_fd)

    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("Corrupt artifact payload") from exc
    _validate_payload(data, filename)
    return data


def _validate_payload(data: object, filename: str) -> None:
    keys = {"version", "case", "repeat", "reason", "visible", "holdout"}
    if (not isinstance(data, dict) or set(data) != keys
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["case"], str) or not _CASE_PATTERN.fullmatch(data["case"])
            or type(data["repeat"]) is not int or not 0 <= data["repeat"] <= MAX_REPEAT_INDEX
            or filename != f"{data['case']}-{data['repeat']}.json"
            or not isinstance(data["reason"], str) or len(data["reason"]) > MAX_REASON_CHARS):
        raise ValueError("Invalid artifact format, identity, or version")
    excluded = {"command", "environment", "stdout", "stderr"}
    allowed = set(TestResult.model_fields) - excluded
    for key in ("visible", "holdout"):
        summary = data[key]
        if summary is None:
            continue
        if not isinstance(summary, dict) or not set(summary) <= allowed:
            raise ValueError("Invalid artifact test summary")
        try:
            result = TestResult.model_validate(
                {**summary, "command": [], "environment": "artifact", "stdout": "", "stderr": ""},
                strict=True,
            )
            if not 0 <= result.duration_seconds < float("inf"):
                raise ValueError("Invalid artifact duration")
        except ValueError as exc:
            raise ValueError("Invalid artifact test summary") from exc


def list_failures(root: Path | str) -> list[str]:
    """Deterministically list all valid failure artifact filenames in root directory."""
    root_path = Path(root) if isinstance(root, str) else root
    if not isinstance(root_path, Path):
        raise TypeError("root must be a Path or str instance")
    if root_path.is_symlink() or not root_path.is_dir():
        raise ValueError("Artifact root must be an existing non-symlink directory")

    items: list[tuple[str, int, str]] = []
    directory_fd = -1
    try:
        directory_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with os.scandir(root_path) as scanner:
            for entry in scanner:
                name = entry.name
                if not _FILENAME_PATTERN.fullmatch(name):
                    continue
                try:
                    stat_info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(stat_info.st_mode):
                        continue
                    match = re.match(r"^([A-Za-z0-9_-]+)-([0-9]+)\.json$", name)
                    if match:
                        case_name, repeat_idx = match.group(1), int(match.group(2))
                        items.append((case_name, repeat_idx, name))
                except OSError:
                    continue
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)

    items.sort(key=lambda x: (x[0], x[1]))
    return [item[2] for item in items]


def summarize_failures(root: Path | str) -> FailureSummary:
    """Summarize all failure artifacts in root, tallying counts, cases, and reasons."""
    filenames = list_failures(root)
    failed_cases: set[str] = set()
    reasons: dict[str, int] = {}
    valid_artifacts: list[str] = []

    root_path = Path(root) if isinstance(root, str) else root
    for filename in filenames:
        try:
            data = read_failure(root_path, filename)
            case = data["case"]
            reason = data["reason"]
            failed_cases.add(case)
            reasons[reason] = reasons.get(reason, 0) + 1
            valid_artifacts.append(filename)
        except (ValueError, OSError):
            continue

    return FailureSummary(
        total_failures=len(valid_artifacts),
        failed_cases=sorted(failed_cases),
        reasons=reasons,
        artifacts=valid_artifacts,
    )


def cleanup_temporary_artifacts(root: Path | str) -> int:
    """Clean up orphaned temporary artifact files (.*.tmp) in root directory."""
    root_path = Path(root) if isinstance(root, str) else root
    if root_path.is_symlink() or not root_path.is_dir():
        raise ValueError("Artifact root must be an existing non-symlink directory")

    removed_count = 0
    directory_fd = -1
    try:
        directory_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for name in os.listdir(root_path):
            if name.startswith(".") and name.endswith(".tmp"):
                try:
                    os.unlink(name, dir_fd=directory_fd)
                    removed_count += 1
                except OSError:
                    continue
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
    return removed_count
