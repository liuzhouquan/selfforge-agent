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
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+-\d+\.json\Z")


def prepare_artifacts(directory: Path, repositories: list[Path]) -> Path:
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

    payload = json.dumps({"version": 1, "case": case, "repeat": repeat, "reason": reason,
                          "visible": details(visible), "holdout": details(holdout)},
                         ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Failure artifact exceeds 128 KiB")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError:
        with suppress(OSError):
            path.unlink()
        raise
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

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Failed to open artifact file: {exc}") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise ValueError("Artifact must be a regular file, not a FIFO or device")
        if st.st_size > MAX_ARTIFACT_BYTES:
            os.close(fd)
            raise ValueError("Artifact file exceeds maximum allowed size")

        with os.fdopen(fd, "rb") as handle:
            content = handle.read(MAX_ARTIFACT_BYTES + 1)
            if len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError("Artifact file exceeds maximum allowed size")
    except (ValueError, OSError):
        raise
    except Exception:
        with suppress(OSError):
            os.close(fd)
        raise

    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Corrupt artifact payload: {exc}") from exc

    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Invalid artifact format or missing version")
    return data
