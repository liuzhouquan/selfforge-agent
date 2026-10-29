"""Temporary detached Git worktrees for base, head, and repair checks."""

import os
import selectors
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic


class WorktreeError(RuntimeError):
    """Git could not prepare or clean a temporary worktree."""


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    if result.returncode:
        raise WorktreeError(result.stderr.strip() or "Git worktree operation failed")
    return result.stdout


def git_bounded(repo: Path, *args: str, max_bytes: int) -> tuple[str, bool]:
    """Read at most max_bytes of Git stdout, stopping a larger patch early."""
    if max_bytes < 1:
        raise ValueError("Git output limit must be positive")
    try:
        process = subprocess.Popen(["git", "-C", str(repo), *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    output = bytearray()
    error = bytearray()
    truncated = False
    deadline = monotonic() + 30
    try:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise WorktreeError("Git worktree operation timed out")
                ready = selector.select(remaining)
                if not ready:
                    raise WorktreeError("Git worktree operation timed out")
                for key, _ in ready:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is process.stdout:
                        room = max_bytes - len(output)
                        output.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated = True
                            break
                    else:
                        error.extend(chunk[:max(0, 4096 - len(error))])
                if truncated:
                    break
        if truncated:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=1)
        else:
            process.wait(timeout=max(0, deadline - monotonic()))
            if process.returncode:
                raise WorktreeError(error.decode("utf-8", errors="replace").strip()
                                    or "Git worktree operation failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"Git worktree operation failed: {exc}") from exc
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    return output.decode("utf-8", errors="replace" if truncated else "strict"), truncated


@contextmanager
def detached_worktree(repo: Path, revision: str, destination: Path) -> Iterator[Path]:
    git(repo, "worktree", "add", "--detach", str(destination), revision)
    try:
        yield destination
    finally:
        git(repo, "worktree", "remove", "--force", str(destination))
