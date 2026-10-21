"""Bounded command execution on the host or in an isolated Docker container."""

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    output: str
    timed_out: bool = False


class Executor(Protocol):
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


class HostExecutor:
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
    def __init__(self, root: Path, image: str = "selfforge-agent-sandbox:latest") -> None:
        self.root = root.resolve()
        if not self.root.is_dir() or not image or any(char.isspace() for char in image):
            raise ValueError("A workspace directory and Docker image are required")
        self.image = image

    def run(self, command: str, timeout: float) -> CommandResult:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        name = "selfforge-" + uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix="selfforge-docker-") as temp:
            staged = Path(temp) / "workspace"
            shutil.copytree(self.root, staged, symlinks=True,
                            ignore=shutil.ignore_patterns(
                                ".git", ".venv", ".env", ".local", ".selfforge", "__pycache__"
                            ))
            argv = [
                "docker", "run", "--rm", "--name", name, "--pull", "never",
                "--network", "none", "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "512m", "--cpus", "1", "--user", f"{uid}:{gid}",
                "--mount", f"type=bind,src={staged},dst=/workspace", "-w", "/workspace",
                self.image, "sh", "-lc", command,
            ]
            try:
                result = _run(argv, None, timeout)
            except KeyboardInterrupt:
                self._cleanup(name)
                raise
            if result.timed_out:
                self._cleanup(name)
            return result

    @staticmethod
    def _cleanup(name: str) -> None:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
