"""Read a local Git comparison without executing repository code."""

import re
import subprocess
from pathlib import Path

from selfforge_agent.models import ChangedFile, ChangeKind, DiffHunk, DiffLine, PRContext

_HEX_SHA = re.compile(r"^[0-9a-fA-F]{7,40}\Z")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024  # 16 MiB


class GitContextError(ValueError):
    """The requested local comparison cannot be read."""


def _git(repo: Path, *args: str) -> bytes:
    if repo.is_symlink():
        raise GitContextError("Repository must not be a symlink")
    if any("\x00" in str(arg) for arg in args):
        raise GitContextError("Git command argument cannot contain null bytes")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitContextError(f"Git failed: {exc}") from exc
    if len(result.stdout) > MAX_GIT_OUTPUT_BYTES or len(result.stderr) > MAX_GIT_OUTPUT_BYTES:
        raise GitContextError("Git command output exceeded limit")
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitContextError(message or "Git command failed")
    return result.stdout


def _commit(repo: Path, revision: str) -> str:
    if not isinstance(revision, str) or "\x00" in revision:
        raise GitContextError("Commit revision cannot contain null bytes")
    rev = revision.strip().lower()
    if not _HEX_SHA.fullmatch(rev):
        raise GitContextError("Commit must be a 7–40 character hexadecimal SHA")
    resolved = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip().lower()
    if not re.fullmatch(r"^[0-9a-f]{40}$", resolved):
        raise GitContextError("Resolved commit is not a 40-character SHA")
    return resolved


def parse_hunks(patch: str) -> list[DiffHunk]:
    if not isinstance(patch, str):
        raise GitContextError("Patch must be a string")
    if "\x00" in patch:
        raise GitContextError("Patch must not contain null bytes")
    lines = patch.splitlines()
    if len(lines) > 50_000:
        raise GitContextError("Patch line count exceeds 50000 line limit")

    hunks: list[DiffHunk] = []
    old_line = new_line = 0
    for line in lines:
        match = _HUNK.match(line)
        if match:
            if len(hunks) >= 5000:
                raise GitContextError("Patch hunk count exceeds 5000 hunk limit")
            try:
                old_line, new_line = int(match[1]), int(match[3])
                if old_line < 0 or new_line < 0 or old_line > 2_000_000_000 or new_line > 2_000_000_000:
                    raise ValueError
            except (ValueError, OverflowError) as exc:
                raise GitContextError("Invalid line number in hunk header") from exc
            hunks.append(DiffHunk(header=line, old_start=old_line, new_start=new_line, lines=[]))
        elif hunks and line.startswith("+"):
            hunks[-1].lines.append(DiffLine(kind=ChangeKind.added, content=line[1:], new_line=new_line))
            new_line += 1
        elif hunks and line.startswith("-"):
            hunks[-1].lines.append(
                DiffLine(kind=ChangeKind.removed, content=line[1:], old_line=old_line)
            )
            old_line += 1
        elif hunks and line.startswith(" "):
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
    if repository.is_symlink():
        raise GitContextError("Repository must be a regular directory, not a symlink")
    if any(p.is_symlink() for p in repository.parents):
        raise GitContextError("Repository path must not contain symlinks")
    repo = repository.expanduser()
    if not repo.is_dir():
        raise GitContextError(f"Repository directory does not exist: {repo}")
    if repo.is_symlink():
        raise GitContextError("Repository must be a regular directory, not a symlink")
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise GitContextError("Path is not a Git working tree")
    root = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip())
    if root.resolve() != repo.resolve():
        raise GitContextError(f"Use the Git working tree root: {root}")
    base_sha, head_sha = _commit(repo, base), _commit(repo, head)
    raw = _git(repo, "diff", "--name-status", "-z", "--find-renames", base_sha, head_sha)
    tokens = raw.decode("utf-8", "surrogateescape").split("\0")
    files: list[ChangedFile] = []
    position = 0
    while position < len(tokens) and tokens[position]:
        status = tokens[position]
        old_path = None
        if status.startswith(("R", "C")):
            old_path, path = tokens[position + 1 : position + 3]
            position += 3
        else:
            path = tokens[position + 1]
            position += 2
        patch = _git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
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
    return PRContext(repository=str(repo), base_sha=base_sha, head_sha=head_sha, files=files)
