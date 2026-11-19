"""Read a local Git comparison without executing repository code."""

from __future__ import annotations

import math
import os
import posixpath
import re
import stat
from pathlib import Path

from selfforge_agent.core.process import run_bounded
from selfforge_agent.models import ChangedFile, ChangeKind, DiffHunk, DiffLine, PRContext

MAX_GIT_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_PATCH_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_HUNKS = 5_000
MAX_DIFF_LINES = 50_000
MAX_LINE_NUMBER = 10_000_000

_HEX_SHA = re.compile(r"^[0-9a-fA-F]{7,40}\Z")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_VALID_STATUS_INITIALS = frozenset({"A", "M", "D", "R", "C", "T", "U"})


class GitContextError(ValueError):
    """The requested local comparison cannot be read."""


def _validate_diff_path(path: str | None, *, label: str = "path") -> str:
    if not isinstance(path, str) or not path.strip():
        raise GitContextError(f"Changed file {label} must be a non-empty string")
    clean = path
    if any(c in clean for c in ("\x00", "\r", "\n", "\t")):
        raise GitContextError(f"Changed file {label} contains control characters: {path!r}")
    if clean.startswith(("/", "\\")):
        raise GitContextError(f"Changed file {label} must not be an absolute path: {path!r}")
    norm = posixpath.normpath(clean)
    if norm == "." or ".." in clean.split("/"):
        raise GitContextError(f"Path traversal detected in {label}: {path!r}")
    return clean


def _git(
    repo: Path,
    *args: str,
    timeout: float = 30,
    max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    if (type(timeout) not in (int, float) or timeout <= 0 or timeout > 300
            or not math.isfinite(timeout)):
        raise GitContextError("Timeout must be a finite positive number")
    if repo.is_symlink() or any(parent.is_symlink() for parent in repo.parents):
        raise GitContextError("Repository path must not contain symlinks")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in args):
        raise GitContextError("Invalid Git command arguments")
    try:
        result = run_bounded(["git", "--literal-pathspecs", "-C", str(repo), *args], timeout=timeout, max_bytes=max_output_bytes)
    except (OSError, ValueError) as exc:
        raise GitContextError(f"Git failed: {exc}") from exc
    if result.stdout_limited or result.stderr_limited:
        raise GitContextError("Git command output exceeded size limit")
    if result.returncode:
        raise GitContextError(result.stderr.decode("utf-8", "replace").strip()[-1000:] or "Git command failed")
    return result.stdout


def _commit(repo: Path, revision: str) -> str:
    if not isinstance(revision, str):
        raise GitContextError("Commit must be a string")
    if "\0" in revision:
        raise GitContextError("Commit must not contain null bytes")
    stripped = revision.strip()
    if not stripped:
        raise GitContextError("Commit must not be empty or whitespace")
    if not _HEX_SHA.fullmatch(stripped):
        raise GitContextError("Commit must be a 7–40 character hexadecimal SHA")
    clean_rev = stripped.lower()
    resolved = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{clean_rev}^{{commit}}").decode().strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise GitContextError("Resolved commit is not a 40-character SHA")
    return resolved


def parse_hunks(patch: str) -> list[DiffHunk]:
    if not isinstance(patch, str):
        raise GitContextError("Patch must be a string")
    if "\x00" in patch:
        raise GitContextError("Patch must not contain null bytes")
    if len(patch) > MAX_PATCH_BYTES or len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise GitContextError(f"Diff patch exceeded maximum size limit of {MAX_PATCH_BYTES} bytes")

    hunks: list[DiffHunk] = []
    old_line = new_line = 0
    total_lines = 0

    lines = patch.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        raise GitContextError(f"Diff patch line limit exceeded ({MAX_DIFF_LINES})")
    for line in lines:
        match = _HUNK.match(line)
        if hunks and not match and (
            (line.startswith(("+", " ")) and new_line > MAX_LINE_NUMBER)
            or (line.startswith(("-", " ")) and old_line > MAX_LINE_NUMBER)
        ):
            raise GitContextError("Diff line number exceeds maximum allowed")
        if match:
            if len(hunks) >= MAX_HUNKS:
                raise GitContextError(f"Diff patch exceeded maximum allowed hunks ({MAX_HUNKS})")
            if len(match[1]) > 8 or len(match[3]) > 8:
                raise GitContextError("Invalid line number in hunk header")
            old_line, new_line = int(match[1]), int(match[3])
            if old_line > MAX_LINE_NUMBER or new_line > MAX_LINE_NUMBER:
                raise GitContextError(
                    f"Diff line number exceeds maximum allowed ({MAX_LINE_NUMBER}): {max(old_line, new_line)}"
                )
            hunks.append(DiffHunk(header=line, old_start=old_line, new_start=new_line, lines=[]))
        elif hunks and line.startswith("+"):
            total_lines += 1
            if total_lines > MAX_DIFF_LINES:
                raise GitContextError(f"Diff patch line limit exceeded ({MAX_DIFF_LINES})")
            hunks[-1].lines.append(DiffLine(kind=ChangeKind.added, content=line[1:], new_line=new_line))
            new_line += 1
        elif hunks and line.startswith("-"):
            total_lines += 1
            if total_lines > MAX_DIFF_LINES:
                raise GitContextError(f"Diff patch line limit exceeded ({MAX_DIFF_LINES})")
            hunks[-1].lines.append(
                DiffLine(kind=ChangeKind.removed, content=line[1:], old_line=old_line)
            )
            old_line += 1
        elif hunks and line.startswith(" "):
            total_lines += 1
            if total_lines > MAX_DIFF_LINES:
                raise GitContextError(f"Diff patch line limit exceeded ({MAX_DIFF_LINES})")
            hunks[-1].lines.append(
                DiffLine(
                    kind=ChangeKind.context,
                    content=line[1:],
                    old_line=old_line,
                    new_line=new_line,
                )
            )
            old_line += 1
            new_line += 1
    return hunks


def build_context(repository: Path, base: str, head: str) -> PRContext:
    if not isinstance(repository, Path):
        repository = Path(repository)

    repository = repository.expanduser()
    if any(parent.is_symlink() for parent in repository.parents):
        raise GitContextError("Repository path must not contain symlinks")
    # Fail-closed symlink and directory check
    try:
        st = os.lstat(repository)
        if stat.S_ISLNK(st.st_mode):
            raise GitContextError(f"Repository path cannot be a symlink: {repository}")
        if not stat.S_ISDIR(st.st_mode):
            raise GitContextError(f"Repository path must be a directory: {repository}")
    except FileNotFoundError:
        raise GitContextError(f"Repository directory does not exist: {repository}")
    except OSError as exc:
        raise GitContextError(f"Cannot access repository directory {repository}: {exc}") from exc

    if _git(repository, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise GitContextError("Path is not a Git working tree")

    root = Path(_git(repository, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repository.resolve():
        raise GitContextError(f"Use the Git working tree root: {root}")

    base_sha, head_sha = _commit(repository, base), _commit(repository, head)
    raw = _git(repository, "diff", "--name-status", "-z", "--find-renames", "--end-of-options", base_sha, head_sha)
    tokens = raw.decode("utf-8", "surrogateescape").split("\0")
    files: list[ChangedFile] = []
    position = 0

    while position < len(tokens) and tokens[position]:
        status = tokens[position]
        if not status or status[0] not in _VALID_STATUS_INITIALS:
            raise GitContextError(f"Invalid status in git diff token stream: {status!r}")

        old_path = None
        if status.startswith(("R", "C")):
            if position + 2 >= len(tokens):
                raise GitContextError(
                    f"Truncated git diff token stream for rename/copy status {status!r}"
                )
            old_token = tokens[position + 1]
            new_token = tokens[position + 2]
            if not old_token or not new_token:
                raise GitContextError(
                    f"Empty path token in git diff token stream for status {status!r}"
                )
            old_path = _validate_diff_path(old_token, label="previous_path")
            path = _validate_diff_path(new_token, label="path")
            position += 3
        else:
            if position + 1 >= len(tokens):
                raise GitContextError(
                    f"Truncated git diff token stream for status {status!r}"
                )
            token = tokens[position + 1]
            if not token:
                raise GitContextError(
                    f"Empty path token in git diff token stream for status {status!r}"
                )
            path = _validate_diff_path(token, label="path")
            position += 2

        patch = _git(
            repository,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
            "--end-of-options",
            base_sha,
            head_sha,
            "--",
            old_path or path,
            *([path] if old_path else []),
        ).decode("utf-8", "replace")

        files.append(
            ChangedFile(
                path=path,
                previous_path=old_path,
                status=status,
                hunks=parse_hunks(patch),
                binary="Binary files" in patch or "GIT binary patch" in patch,
            )
        )

    return PRContext(repository=str(repository), base_sha=base_sha, head_sha=head_sha, files=files)
