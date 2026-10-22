"""Bounded command execution on the host or in an isolated Docker container."""

import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
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
            shutil.copytree(self.root, staged, symlinks=True,
                            ignore=shutil.ignore_patterns(
                                ".git", ".venv", ".env", ".env.*", ".local", ".selfforge",
                                ".workspaces",
                                ".aws", ".ssh", ".npmrc", ".pypirc", ".netrc",
                                "__pycache__", "node_modules"
                            ))
            archive = Path(temp) / "workspace.tar"
            with tarfile.open(archive, "w") as bundle:
                for child in staged.iterdir():
                    bundle.add(child, arcname=child.name)
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
