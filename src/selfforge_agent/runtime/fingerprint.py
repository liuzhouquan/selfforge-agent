"""Bounded content snapshots without following workspace symlinks."""

import hashlib
import math
import os
import stat
import time
import unicodedata
from pathlib import Path
from threading import Event

from selfforge_agent.runtime.execution import (
    MAX_STAGED_BYTES,
    MAX_STAGED_ENTRIES,
    workspace_entry_excluded,
)

MAX_SNAPSHOT_DEPTH = 128
MAX_SINGLE_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB limit per regular file

__all__ = [
    "MAX_SINGLE_FILE_BYTES",
    "MAX_SNAPSHOT_DEPTH",
    "WorkspaceSnapshotError",
    "workspace_fingerprint",
]


class WorkspaceSnapshotError(RuntimeError):
    """No stable, complete snapshot was available; do not retain evidence."""


def workspace_fingerprint(
    root: Path | str,
    *,
    deadline: float | None = None,
    cancel: Event | None = None,
) -> str:
    """Hash paths, types, permissions, link targets and regular-file contents.

    Directory descriptors and O_NOFOLLOW prevent swapped symlinks from causing
    reads outside the workspace. This is a bounded observation, not an atomic
    snapshot or a sandbox for trusted Host code.
    """
    if isinstance(root, str):
        if "\0" in root:
            raise ValueError("null byte in root path")
        if not root.strip():
            raise ValueError("root cannot be empty or whitespace")
        root_path = Path(root)
    elif isinstance(root, Path):
        if "\0" in str(root):
            raise ValueError("null byte in root path")
        raw_parts = getattr(root, "_raw_paths", [str(root)])
        if not str(root).strip() or any(isinstance(p, str) and not p.strip() for p in raw_parts):
            raise ValueError("root cannot be empty or whitespace")
        root_path = root
    else:
        raise TypeError("root must be a Path or str instance")

    if cancel is not None and not isinstance(cancel, Event):
        raise TypeError("cancel must be an Event instance or None")

    now = time.monotonic()
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise TypeError("deadline must be a float or int or None")
        if not math.isfinite(deadline):
            raise ValueError("deadline must be a finite number")
        effective_deadline = min(float(deadline), now + 10.0)
    else:
        effective_deadline = now + 10.0

    digest = hashlib.sha256(b"selfforge-workspace-v1\0")
    entries = size = 0

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise KeyboardInterrupt
        if time.monotonic() >= effective_deadline:
            raise TimeoutError("Workspace fingerprint timed out")

    def field(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    def identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    ancestor_dirs: set[tuple[int, int]] = set()

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal entries, size
        check()
        if depth > MAX_SNAPSHOT_DEPTH:
            raise WorkspaceSnapshotError("Workspace fingerprint depth limit exceeded")
        before = os.fstat(fd)
        current_id = (before.st_dev, before.st_ino)
        if current_id in ancestor_dirs:
            raise WorkspaceSnapshotError("Workspace fingerprint detected directory cycle")
        ancestor_dirs.add(current_id)

        try:
            names: list[str] = []
            with os.scandir(fd) as listing:
                for entry in listing:
                    check()
                    if workspace_entry_excluded(entry.name):
                        continue
                    entries += 1
                    if entries > MAX_STAGED_ENTRIES:
                        raise WorkspaceSnapshotError("Workspace fingerprint entry limit exceeded")
                    names.append(entry.name)

            for name in sorted(names, key=lambda n: (unicodedata.normalize("NFC", n), n)):
                check()
                path = prefix + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                field(os.fsencode(path))
                if stat.S_ISDIR(info.st_mode):
                    field(str(stat.S_IMODE(info.st_mode)).encode())
                    field(b"directory")
                    if (info.st_dev, info.st_ino) in ancestor_dirs:
                        raise WorkspaceSnapshotError("Workspace fingerprint detected directory cycle")
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=fd,
                    )
                    try:
                        child_st = os.fstat(child)
                        if identity(info) != identity(child_st):
                            raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                        child_id = (child_st.st_dev, child_st.st_ino)
                        if child_id in ancestor_dirs:
                            raise WorkspaceSnapshotError("Workspace fingerprint detected directory cycle")
                        walk(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISLNK(info.st_mode):
                    field(str(0o777).encode())
                    field(b"symlink")
                    field(os.fsencode(os.readlink(name, dir_fd=fd)))
                    if identity(info) != identity(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                        raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                elif stat.S_ISREG(info.st_mode):
                    field(str(stat.S_IMODE(info.st_mode)).encode())
                    field(b"file")
                    if info.st_size < 0:
                        raise WorkspaceSnapshotError("Workspace fingerprint encountered invalid file size")
                    if info.st_size > MAX_SINGLE_FILE_BYTES:
                        raise WorkspaceSnapshotError("Workspace fingerprint file size limit exceeded")
                    field(str(info.st_size).encode())
                    if size + info.st_size > MAX_STAGED_BYTES:
                        raise WorkspaceSnapshotError("Workspace fingerprint byte limit exceeded")
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                        dir_fd=fd,
                    )
                    file_bytes = 0
                    with os.fdopen(file_fd, "rb") as source:
                        if identity(info) != identity(os.fstat(source.fileno())):
                            raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                        while block := source.read(64 * 1024):
                            check()
                            size += len(block)
                            file_bytes += len(block)
                            if size > MAX_STAGED_BYTES:
                                raise WorkspaceSnapshotError("Workspace fingerprint byte limit exceeded")
                            if file_bytes > MAX_SINGLE_FILE_BYTES:
                                raise WorkspaceSnapshotError("Workspace fingerprint file size limit exceeded")
                            digest.update(block)
                        if (
                            file_bytes != info.st_size
                            or identity(info) != identity(os.fstat(source.fileno()))
                        ):
                            raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                    if identity(info) != identity(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                        raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                else:
                    raise WorkspaceSnapshotError("Workspace fingerprint contains unsupported entry")
            if identity(before) != identity(os.fstat(fd)):
                raise WorkspaceSnapshotError("Workspace changed during fingerprint")
        finally:
            ancestor_dirs.remove(current_id)

    try:
        check()
        clean_root_str = str(root_path).rstrip(os.sep) or os.sep
        if os.path.islink(clean_root_str):
            raise WorkspaceSnapshotError("Workspace root cannot be a symlink")

        root_abs = Path(clean_root_str).absolute()
        clean_abs_str = str(root_abs).rstrip(os.sep) or os.sep
        if os.path.islink(clean_abs_str):
            raise WorkspaceSnapshotError("Workspace root cannot be a symlink")

        root_fd = os.open(clean_abs_str, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            root_st = os.fstat(root_fd)
            lstat_st = os.lstat(clean_abs_str)
            if stat.S_ISLNK(lstat_st.st_mode) or (lstat_st.st_dev, lstat_st.st_ino) != (root_st.st_dev, root_st.st_ino):
                raise WorkspaceSnapshotError("Workspace root changed or is a symlink")

            field(os.fsencode(root_abs))
            field(str(stat.S_IMODE(root_st.st_mode)).encode())
            walk(root_fd, "", 0)
            if identity(root_st) != identity(os.lstat(clean_abs_str)):
                raise WorkspaceSnapshotError("Workspace root changed during fingerprint")
        finally:
            os.close(root_fd)
        check()
    except OSError as exc:
        if isinstance(exc, TimeoutError):
            raise
        # Keep private paths and OS error bodies out of traces.
        raise WorkspaceSnapshotError("Workspace fingerprint could not read a complete tree") from exc

    return digest.hexdigest()
