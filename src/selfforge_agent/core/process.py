"""Bounded native process capture shared by read-only Git and GitHub operations."""

import math
import os
import selectors
import signal
import subprocess
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_limited: bool = False
    stderr_limited: bool = False


def run_bounded(command: list[str], *, cwd=None, timeout: float = 30,
                max_bytes: int, stderr_bytes: int | None = None) -> BoundedProcessResult:
    if (type(timeout) not in (int, float) or timeout <= 0 or timeout > 3600
            or not math.isfinite(timeout)):
        raise ValueError("Process timeout must be finite and positive")
    error_limit = max_bytes if stderr_bytes is None else stderr_bytes
    if any(type(value) is not int or not 1 <= value <= 50 * 1024 * 1024
           for value in (max_bytes, error_limit)):
        raise ValueError("Process output limit must be a positive integer up to 50 MiB")
    if not command or any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise ValueError("Invalid process arguments")
    deadline = monotonic() + timeout
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=(os.name == "posix"))
    buffers = [bytearray(), bytearray()]
    limits = [max_bytes, error_limit]
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
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
    return BoundedProcessResult(process.returncode, bytes(buffers[0]), bytes(buffers[1]), *limited)
