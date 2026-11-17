"""Bounded native process capture shared by read-only Git and GitHub operations."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

MAX_COMMAND_ARGS = 1000
MAX_ARG_LENGTH = 128 * 1024
MAX_TIMEOUT_SECONDS = 3600.0
MAX_OUTPUT_LIMIT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_limited: bool = False
    stderr_limited: bool = False


def _validate_command(command: list[str]) -> None:
    if not isinstance(command, list):
        raise TypeError("command must be a list of strings")
    if not command:
        raise ValueError("command must not be empty")
    if len(command) > MAX_COMMAND_ARGS:
        raise ValueError(f"command has too many arguments (maximum allowed is {MAX_COMMAND_ARGS})")
    for i, arg in enumerate(command):
        if not isinstance(arg, str):
            raise TypeError(f"command argument {i} must be a string, got {type(arg).__name__}")
        if "\0" in arg:
            raise ValueError(f"command argument {i} must not contain null bytes")
        if len(arg) > MAX_ARG_LENGTH or len(os.fsencode(arg)) + 1 > MAX_ARG_LENGTH:
            raise ValueError(f"command argument {i} is too long (maximum length is {MAX_ARG_LENGTH} bytes)")
    if not command[0].strip():
        raise ValueError("command binary name must not be empty or whitespace")


def _validate_cwd(cwd: str | Path | None) -> Path | None:
    if cwd is None:
        return None
    if not isinstance(cwd, (str, Path)):
        raise TypeError("cwd must be a str, Path, or None")
    if "\0" in str(cwd):
        raise ValueError("cwd must not contain null bytes")
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        raise FileNotFoundError(f"cwd does not exist: {cwd}")
    if not cwd_path.is_dir():
        raise ValueError(f"cwd must be a directory: {cwd}")
    if cwd_path.is_symlink() or any(p.is_symlink() for p in (cwd_path, *cwd_path.parents)):
        raise ValueError(f"cwd must not be a symlink or inside a symlinked directory: {cwd}")
    return cwd_path


def _validate_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    if not isinstance(env, dict):
        raise TypeError("env must be a dictionary or None")
    clean_env: dict[str, str] = {}
    for k, v in env.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError("env keys and values must be strings")
        if "\0" in k or "\0" in v:
            raise ValueError("env keys and values must not contain null bytes")
        clean_env[k] = v
    return clean_env


def _validate_timeout(timeout: float) -> float:
    if (
        type(timeout) is bool
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
        or not math.isfinite(timeout)
    ):
        raise ValueError(
            f"Process timeout must be a finite positive number up to {int(MAX_TIMEOUT_SECONDS)} seconds"
        )
    return float(timeout)


def _validate_output_limit(limit: int, name: str) -> int:
    if (
        type(limit) is bool
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_OUTPUT_LIMIT_BYTES
    ):
        raise ValueError(
            f"Process output limit for {name} must be a positive integer up to {MAX_OUTPUT_LIMIT_BYTES} bytes (50 MiB)"
        )
    return limit


def run_bounded(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int,
    stderr_bytes: int | None = None,
) -> BoundedProcessResult:
    _validate_command(command)
    cwd_path = _validate_cwd(cwd)
    clean_env = _validate_env(env)
    timeout_val = _validate_timeout(timeout)
    stdout_limit = _validate_output_limit(max_bytes, "max_bytes")
    error_limit = (
        stdout_limit
        if stderr_bytes is None
        else _validate_output_limit(stderr_bytes, "stderr_bytes")
    )

    deadline = monotonic() + timeout_val
    process = subprocess.Popen(
        command,
        cwd=cwd_path,
        env=clean_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    buffers = [bytearray(), bytearray()]
    limits = [stdout_limit, error_limit]
    limited = [False, False]
    try:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 0)
            selector.register(process.stderr, selectors.EVENT_READ, 1)
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Command timed out")
                events = selector.select(remaining)
                if not events:
                    raise TimeoutError("Command timed out")
                for key, _ in events:
                    index = key.data
                    room = limits[index] - len(buffers[index])
                    chunk = os.read(key.fileobj.fileno(), min(8192, room + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffers[index].extend(chunk[:room])
                    if len(chunk) > room:
                        limited[index] = True
                        break
                if any(limited):
                    break
        if not any(limited):
            try:
                process.wait(timeout=max(0.001, deadline - monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("Command timed out") from exc
    finally:
        # Also clean up children retaining pipes after their parent has exited.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        elif process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if process.stdout:
            try:
                process.stdout.close()
            except OSError:
                pass
        if process.stderr:
            try:
                process.stderr.close()
            except OSError:
                pass

    returncode = process.returncode if process.returncode is not None else -1
    return BoundedProcessResult(returncode, bytes(buffers[0]), bytes(buffers[1]), *limited)
