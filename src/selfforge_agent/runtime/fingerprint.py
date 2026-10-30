"""Bounded content snapshots without following workspace symlinks."""

import hashlib
import os
import stat
import time
from pathlib import Path
from threading import Event

from selfforge_agent.runtime.execution import (
    MAX_STAGED_BYTES,
    MAX_STAGED_ENTRIES,
    workspace_entry_excluded,
)

MAX_SNAPSHOT_DEPTH = 128


class WorkspaceSnapshotError(RuntimeError):
    """No stable, complete snapshot was available; do not retain evidence."""


def workspace_fingerprint(root: Path, *, deadline: float | None = None,
                          cancel: Event | None = None) -> str:
    """Hash paths, types, permissions, link targets and regular-file contents.

    Directory descriptors and O_NOFOLLOW prevent swapped symlinks from causing
    reads outside the workspace. This is a bounded observation, not an atomic
    snapshot or a sandbox for trusted Host code.
    """
    now = time.monotonic()
    deadline = min(deadline, now + 10) if deadline is not None else now + 10
    digest = hashlib.sha256(b"selfforge-workspace-v1\0")
    entries = size = 0

    def check():
        if cancel is not None and cancel.is_set():
            raise KeyboardInterrupt
        if time.monotonic() >= deadline:
            raise TimeoutError("Workspace fingerprint timed out")

    def field(value: bytes):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    def walk(fd: int, prefix: str, depth: int):
        nonlocal entries, size
        check()
        if depth > MAX_SNAPSHOT_DEPTH:
            raise WorkspaceSnapshotError("Workspace fingerprint depth limit exceeded")
        before = os.fstat(fd)
        names = []
        with os.scandir(fd) as listing:
            for entry in listing:
                check()
                if workspace_entry_excluded(entry.name):
                    continue
                entries += 1
                if entries > MAX_STAGED_ENTRIES:
                    raise WorkspaceSnapshotError("Workspace fingerprint entry limit exceeded")
                names.append(entry.name)
        for name in sorted(names):
            check()
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            field(os.fsencode(path))
            field(str(stat.S_IMODE(info.st_mode)).encode())
            if stat.S_ISDIR(info.st_mode):
                field(b"directory")
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if identity(info) != identity(os.fstat(child)):
                        raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                    walk(child, path + "/", depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(info.st_mode):
                field(b"symlink")
                field(os.fsencode(os.readlink(name, dir_fd=fd)))
                if identity(info) != identity(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    raise WorkspaceSnapshotError("Workspace changed during fingerprint")
            elif stat.S_ISREG(info.st_mode):
                field(b"file")
                field(str(info.st_size).encode())
                if size + info.st_size > MAX_STAGED_BYTES:
                    raise WorkspaceSnapshotError("Workspace fingerprint byte limit exceeded")
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
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
                        digest.update(block)
                    if (file_bytes != info.st_size
                            or identity(info) != identity(os.fstat(source.fileno()))):
                        raise WorkspaceSnapshotError("Workspace changed during fingerprint")
                if identity(info) != identity(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    raise WorkspaceSnapshotError("Workspace changed during fingerprint")
            else:
                raise WorkspaceSnapshotError("Workspace fingerprint contains unsupported entry")
        if identity(before) != identity(os.fstat(fd)):
            raise WorkspaceSnapshotError("Workspace changed during fingerprint")

    try:
        check()
        root = root.absolute()
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            field(os.fsencode(root))
            field(str(stat.S_IMODE(os.fstat(root_fd).st_mode)).encode())
            walk(root_fd, "", 0)
        finally:
            os.close(root_fd)
        check()
    except OSError as exc:
        if isinstance(exc, TimeoutError):
            raise
        # Keep private paths and OS error bodies out of traces.
        raise WorkspaceSnapshotError("Workspace fingerprint could not read a complete tree") from exc
    return digest.hexdigest()
