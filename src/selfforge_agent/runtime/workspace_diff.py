"""Bounded, read-only diff of a persistent Coding worktree against its base commit."""

import difflib
from pathlib import Path

from selfforge_agent.coding.worktree import WorktreeError, git_bounded
from selfforge_agent.runtime.tools import _protected
from selfforge_agent.runtime.workspace import CodingWorkspace

MAX_FILES = 40
MAX_FILE_BYTES = 64_000
MAX_PATCH_CHARS = 24_000
MAX_PATH_LIST_BYTES = 256_000
MAX_PATCH_BYTES = MAX_PATCH_CHARS * 4


class DiffLimitError(WorktreeError):
    """The changed-path list cannot be represented within the response budget."""


def _visible(path: str) -> bool:
    return bool(path) and not any(_protected(part) for part in Path(path).parts)


def _paths(workspace: CodingWorkspace, *args: str) -> list[str]:
    output, truncated = git_bounded(workspace.path, *args, max_bytes=MAX_PATH_LIST_BYTES)
    if truncated:
        raise DiffLimitError("Too many changed paths to display a complete diff")
    return [path for path in output.split("\0") if path]


def workspace_diff(workspace: CodingWorkspace) -> dict:
    """Include committed/working-tree changes and safe untracked text files."""
    tracked = _paths(workspace, "diff", "--name-only", "-z", workspace.base_sha)
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    files = []
    omitted = 0
    for path in sorted(set(tracked + untracked)):
        if not _visible(path) or len(files) == MAX_FILES:
            omitted += 1
            continue
        current = workspace.path / path
        output_truncated = False
        if path in untracked:
            if current.is_symlink() or not current.is_file() or current.stat().st_size > MAX_FILE_BYTES:
                patch = "[Untracked non-text, symlink, or large file omitted]"
            else:
                try:
                    lines = current.read_text(encoding="utf-8").splitlines(keepends=True)
                except UnicodeError:
                    patch = "[Untracked non-UTF-8 file omitted]"
                else:
                    patch = "".join(difflib.unified_diff([], lines, fromfile="/dev/null",
                                                          tofile=f"b/{path}"))
            status = "untracked"
        else:
            patch, output_truncated = git_bounded(
                workspace.path, "diff", "--no-ext-diff", "--no-textconv",
                "--unified=3", workspace.base_sha, "--", path, max_bytes=MAX_PATCH_BYTES)
            status = "tracked"
        truncated = len(patch) > MAX_PATCH_CHARS or output_truncated
        files.append({"path": path, "status": status,
                      "patch": patch[:MAX_PATCH_CHARS] + ("\n[Patch truncated]" if truncated else ""),
                      "truncated": truncated})
    return {"session_id": workspace.session_id, "branch": workspace.branch,
            "base_sha": workspace.base_sha, "files": files, "omitted_files": omitted}
