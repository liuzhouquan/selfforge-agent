"""Explicit, private test-diagnostic snapshots; never model or source archives."""

import json
import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from selfforge_agent.models import TestResult

MAX_ARTIFACT_BYTES = 128 * 1024
MAX_REPEAT_INDEX = 100_000
_CASE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}\Z")
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}-[0-9]{1,6}\.json\Z")


def prepare_artifacts(directory: Path, repositories: list[Path]) -> Path:
    directory = directory.expanduser()
    if directory.is_symlink():
        raise ValueError("Failure artifacts directory must be a non-symlink")
    parent = directory.expanduser().resolve()
    for repository in repositories:
        repo_resolved = repository.resolve()
        if parent == repo_resolved or parent.is_relative_to(repo_resolved):
            raise ValueError("Failure artifacts must stay outside evaluated repositories")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = parent / uuid4().hex
    root.mkdir(mode=0o700)
    return root


def write_failure(root: Path, *, case: str, repeat: int, reason: str,
                  visible: TestResult | None, holdout: TestResult | None) -> str:
    if not isinstance(case, str) or not _CASE_PATTERN.fullmatch(case):
        raise ValueError(f"Invalid case name: {case!r}")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0 or repeat > MAX_REPEAT_INDEX:
        raise ValueError(f"Invalid repeat index: {repeat!r}")
    if not isinstance(reason, str) or len(reason) > 4096:
        raise ValueError("Invalid failure reason")
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

    data = {"version": 1, "case": case, "repeat": repeat, "reason": reason,
            "visible": details(visible), "holdout": details(holdout)}
    _validate_payload(data, filename)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Failure artifact exceeds 128 KiB")

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
            # link is atomic and refuses an existing destination, including symlinks.
            os.link(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                    follow_symlinks=False)
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
            or not isinstance(data["reason"], str) or len(data["reason"]) > 4096):
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
