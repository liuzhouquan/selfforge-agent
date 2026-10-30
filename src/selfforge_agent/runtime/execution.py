"""Bounded command execution on the host or in an isolated Docker container."""

import fnmatch
import os
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol
from uuid import uuid4

MAX_STAGED_ENTRIES = 8_000
MAX_STAGED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
_EXCLUDED = (
    ".git", ".venv", ".env", ".env.*", ".local", ".selfforge", ".workspaces",
    ".aws", ".ssh", ".npmrc", ".pypirc", ".netrc", "__pycache__", "node_modules",
)


class _StageTimedOut(Exception):
    pass


class _StageCancelled(Exception):
    pass


def _check_staging(deadline: float, cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise _StageCancelled
    if time.monotonic() >= deadline:
        raise _StageTimedOut


class _CheckedReader:
    def __init__(self, source, deadline: float, cancel: Event | None) -> None:
        self.source = source
        self.deadline = deadline
        self.cancel = cancel

    def read(self, size: int) -> bytes:
        _check_staging(self.deadline, self.cancel)
        block = self.source.read(size)
        _check_staging(self.deadline, self.cancel)
        return block


def _stage_workspace(root: Path, staged: Path, deadline: float,
                     cancel: Event | None) -> None:
    entries = 0
    estimated_bytes = 0
    copied_bytes = 0
    staged.mkdir()
    pending = [(root, staged)]
    directories = [(root, staged)]
    while pending:
        source_dir, destination_dir = pending.pop()
        _check_staging(deadline, cancel)
        with os.scandir(source_dir) as listing:
            for entry in listing:
                _check_staging(deadline, cancel)
                if any(fnmatch.fnmatch(entry.name, pattern) for pattern in _EXCLUDED):
                    continue
                entries += 1
                if entries > MAX_STAGED_ENTRIES:
                    raise ValueError("Workspace staging entry limit exceeded")
                info = entry.stat(follow_symlinks=False)
                source = Path(entry.path)
                destination = destination_dir / entry.name
                if stat.S_ISDIR(info.st_mode):
                    destination.mkdir()
                    pending.append((source, destination))
                    directories.append((source, destination))
                elif stat.S_ISLNK(info.st_mode):
                    destination.symlink_to(os.readlink(source))
                elif stat.S_ISREG(info.st_mode):
                    estimated_bytes += info.st_size
                    if estimated_bytes > MAX_STAGED_BYTES:
                        raise ValueError("Workspace staging byte limit exceeded")
                    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
                    fd = os.open(source, flags)
                    with os.fdopen(fd, "rb") as reader, destination.open("wb") as writer:
                        if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                            raise ValueError("Workspace staging contains an unsupported entry")
                        while block := reader.read(64 * 1024):
                            _check_staging(deadline, cancel)
                            copied_bytes += len(block)
                            if copied_bytes > MAX_STAGED_BYTES:
                                raise ValueError("Workspace staging byte limit exceeded")
                            writer.write(block)
                            _check_staging(deadline, cancel)
                    shutil.copystat(source, destination, follow_symlinks=False)
                else:
                    raise ValueError("Workspace staging contains an unsupported entry")
    for source, destination in reversed(directories):
        _check_staging(deadline, cancel)
        shutil.copystat(source, destination, follow_symlinks=False)
    _check_staging(deadline, cancel)


def _archive_workspace(staged: Path, archive: Path, deadline: float,
                       cancel: Event | None) -> None:
    with tarfile.open(archive, "w", dereference=False) as bundle:
        pending = [staged]
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                _check_staging(deadline, cancel)
                info = bundle.gettarinfo(str(path), arcname=path.relative_to(staged).as_posix())
                if info is None:
                    raise ValueError("Workspace staging contains an unsupported entry")
                if info.isfile():
                    with path.open("rb") as source:
                        bundle.addfile(info, _CheckedReader(source, deadline, cancel))
                else:
                    bundle.addfile(info)
                if bundle.fileobj.tell() > MAX_ARCHIVE_BYTES:
                    raise ValueError("Workspace staging archive byte limit exceeded")
                _check_staging(deadline, cancel)
                if info.isdir():
                    pending.append(path)
    _check_staging(deadline, cancel)


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    output: str
    timed_out: bool = False


class Executor(Protocol):
    persists_writes: bool

    def run(self, command: str, timeout: float) -> CommandResult: ...


def _run(argv: list[str], cwd: Path | None, timeout: float) -> CommandResult:
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return CommandResult(None, output[-12_000:], timed_out=True)
    except OSError as exc:
        raise RuntimeError(f"Command executor is unavailable: {exc}") from exc
    return CommandResult(result.returncode, (result.stdout + result.stderr)[-12_000:])


def resolve_docker_image(image: str, timeout: float = 10) -> str:
    """Resolve a local reference without pulling or running its code."""
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise ValueError("Invalid Docker image reference")
    if timeout <= 0:
        raise TimeoutError("Docker image inspection timed out")
    result = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], None, timeout)
    if result.timed_out:
        raise TimeoutError("Docker image inspection timed out")
    image_id = result.output.strip()
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Docker image is unavailable locally; restore the image before resuming")
    return image_id


class HostExecutor:
    persists_writes = True

    def __init__(self, root: Path, *, trusted: bool = False) -> None:
        if not trusted:
            raise ValueError("Host execution requires explicit trust")
        self.root = root.resolve()

    def run(self, command: str, timeout: float) -> CommandResult:
        process = subprocess.Popen(["sh", "-lc", command], cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name == "posix"))
        try:
            output, _ = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            output, _ = process.communicate()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return CommandResult(None, output.decode("utf-8", "replace")[-12_000:],
                                 timed_out=True)
        return CommandResult(process.returncode, output.decode("utf-8", "replace")[-12_000:])


class DockerExecutor:
    persists_writes = False

    def __init__(self, root: Path, image: str | None = None,
                 *, cancel: Event | None = None) -> None:
        self.root = root.resolve()
        self.image_explicit = image is not None
        image = image if image is not None else "selfforge-agent-sandbox:latest"
        if not self.root.is_dir() or not image or any(char.isspace() for char in image):
            raise ValueError("A workspace directory and Docker image are required")
        self.image = image
        self.cancel = cancel

    def run(self, command: str, timeout: float) -> CommandResult:
        result, _ = self._execute(command, timeout)
        return result

    def run_with_artifact(self, command: str, timeout: float, name: str) -> tuple[CommandResult, bytes | None]:
        """Return one container-created artifact without trusting command stdout as evidence."""
        if name != Path(name).name or name in {"", ".", ".."}:
            raise ValueError("Artifact name must be a plain filename")
        return self._execute(command, timeout, artifact=name)

    def _execute(self, command: str, timeout: float, *, artifact: str | None = None) -> tuple[CommandResult, bytes | None]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        name = "selfforge-" + uuid4().hex[:12]
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            return max(0.01, deadline - time.monotonic())

        with tempfile.TemporaryDirectory(prefix="selfforge-docker-") as temp:
            staged = Path(temp) / "workspace"
            archive = Path(temp) / "workspace.tar"
            try:
                _stage_workspace(self.root, staged, deadline, self.cancel)
                _archive_workspace(staged, archive, deadline, self.cancel)
            except _StageTimedOut:
                return CommandResult(None, "Workspace staging timed out", True), None
            except _StageCancelled:
                return CommandResult(None, "Workspace staging cancelled"), None
            except ValueError as exc:
                return CommandResult(1, str(exc)), None
            argv = [
                "docker", "run", "-d", "--rm", "--name", name, "--pull", "never",
                "--network", "none", "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "512m", "--cpus", "1", "--user", f"{uid}:{gid}",
                "--tmpfs", "/workspace:rw,nosuid,mode=1777,size=128m",
                "--tmpfs", "/artifacts:rw,nosuid,mode=1777,size=8m",
                self.image, "sleep", "600",
            ]
            try:
                started = _run(argv, None, remaining())
                if started.returncode != 0 or started.timed_out:
                    return started, None
                try:
                    with archive.open("rb") as stream:
                        copied = subprocess.run(
                            ["docker", "exec", "-i", "-u", f"{uid}:{gid}", name,
                             "tar", "-xf", "-", "-C", "/workspace"],
                            stdin=stream, capture_output=True, text=True,
                            errors="replace", timeout=remaining(), check=False)
                except subprocess.TimeoutExpired:
                    return CommandResult(None, "Workspace transfer timed out", True), None
                if copied.returncode:
                    return CommandResult(copied.returncode,
                                         (copied.stdout + copied.stderr)[-12_000:]), None
                result = _run(["docker", "exec", "-u", f"{uid}:{gid}", "-w", "/workspace",
                               name, "sh", "-lc", command], None, remaining())
                evidence = None
                if artifact and not result.timed_out:
                    try:
                        fetched = subprocess.run(
                            ["docker", "exec", "-u", f"{uid}:{gid}", name,
                             "cat", f"/artifacts/{artifact}"],
                            capture_output=True, timeout=remaining(), check=False)
                    except subprocess.TimeoutExpired:
                        fetched = None
                    if fetched is not None and fetched.returncode == 0 and len(fetched.stdout) <= 4_000_000:
                        evidence = fetched.stdout
                return result, evidence
            finally:
                self._cleanup(name)

    @staticmethod
    def _cleanup(name: str) -> None:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
