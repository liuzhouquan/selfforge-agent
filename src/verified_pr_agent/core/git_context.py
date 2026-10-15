"""Read a local Git comparison without executing repository code."""

import re
import subprocess
from pathlib import Path

from verified_pr_agent.models import ChangedFile, ChangeKind, DiffHunk, DiffLine, PRContext

_HEX_SHA = re.compile(r"[0-9a-fA-F]{7,40}\Z")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitContextError(ValueError):
    """The requested local comparison cannot be read."""


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitContextError(f"Git failed: {exc}") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitContextError(message or "Git command failed")
    return result.stdout


def _commit(repo: Path, revision: str) -> str:
    if not _HEX_SHA.fullmatch(revision):
        raise GitContextError("Commit must be a 7–40 character hexadecimal SHA")
    return _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()


def parse_hunks(patch: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    old_line = new_line = 0
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            old_line, new_line = int(match[1]), int(match[3])
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
    repo = repository.expanduser().resolve()
    if not repo.is_dir():
        raise GitContextError(f"Repository directory does not exist: {repo}")
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise GitContextError("Path is not a Git working tree")
    root = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repo:
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
