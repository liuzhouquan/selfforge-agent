"""Bounded, read-only diff of a persistent Coding worktree against its base commit."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from selfforge_agent.coding.worktree import WorktreeError, git_bounded
from selfforge_agent.runtime.tools import ToolRegistry, _protected
from selfforge_agent.runtime.workspace import CodingWorkspace

MAX_FILES = 40
MAX_FILE_BYTES = 64_000
MAX_PATCH_CHARS = 24_000
MAX_PATH_LIST_BYTES = 256_000
MAX_PATCH_BYTES = MAX_PATCH_CHARS * 4
MAX_TOTAL_DIFF_BYTES = 256_000

_COMMIT = re.compile(r"^[0-9a-f]{40}\Z")


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
    if not isinstance(workspace, CodingWorkspace):
        raise TypeError(f"workspace must be a CodingWorkspace, got {type(workspace).__name__}")
    if not isinstance(workspace.base_sha, str) or not _COMMIT.fullmatch(workspace.base_sha):
        raise ValueError(f"Invalid base commit SHA: {workspace.base_sha!r}")

    tracked = _paths(workspace, "diff", "--name-only", "-z", "--end-of-options", workspace.base_sha)
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    files: list[dict] = []
    omitted = 0
    total_diff_bytes = 0
    reader = ToolRegistry(workspace.path)

    for path in sorted(set(tracked + untracked)):
        if not _visible(path) or len(files) == MAX_FILES:
            omitted += 1
            continue

        remaining_budget = MAX_TOTAL_DIFF_BYTES - total_diff_bytes
        if remaining_budget <= 0:
            omitted += 1
            continue

        current = workspace.path / path
        output_truncated = False
        if path in untracked:
            candidate = workspace.path
            has_symlink = False
            for part in Path(path).parts:
                candidate /= part
                if candidate.is_symlink():
                    has_symlink = True
                    break
            if not has_symlink:
                try:
                    resolved = current.resolve(strict=True)
                    if (not resolved.is_relative_to(workspace.path)
                            or any(_protected(p) for p in resolved.relative_to(workspace.path).parts)):
                        has_symlink = True
                except OSError:
                    has_symlink = True

            if has_symlink or not current.is_file():
                patch = "[Untracked non-text, symlink, or large file omitted]"
            else:
                try:
                    with reader._open_source(current) as source:
                        raw = source.read(MAX_FILE_BYTES + 1)
                    if len(raw) > MAX_FILE_BYTES:
                        raise ValueError("File exceeds read limit")
                    if b"\x00" in raw:
                        patch = "[Untracked binary file omitted]"
                    else:
                        lines = raw.decode("utf-8").splitlines(keepends=True)
                        patch = "".join(
                            difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{path}")
                        )
                except UnicodeDecodeError:
                    patch = "[Untracked non-UTF-8 file omitted]"
                except ValueError:
                    patch = "[Untracked non-text, symlink, or large file omitted]"
                except OSError:
                    patch = "[Untracked unreadable file omitted]"
            status = "untracked"
        else:
            patch, output_truncated = git_bounded(
                workspace.path,
                "--literal-pathspecs",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=3",
                "--end-of-options",
                workspace.base_sha,
                "--",
                path,
                max_bytes=MAX_PATCH_BYTES,
            )
            status = "tracked"
            if "\x00" in patch or patch.startswith("Binary files ") or ("differ\n" in patch and "Binary files" in patch):
                patch = "[Binary file diff omitted]"

        truncated = len(patch) > MAX_PATCH_CHARS or output_truncated
        patch_slice = patch[:MAX_PATCH_CHARS] + ("\n[Patch truncated]" if truncated else "")
        patch_bytes = patch_slice.encode("utf-8", errors="replace")
        if len(patch_bytes) > remaining_budget:
            marker = "\n[Patch truncated: total diff budget reached]"
            marker_bytes = marker.encode()[:remaining_budget]
            patch_slice = (patch_bytes[:remaining_budget - len(marker_bytes)].decode("utf-8", errors="ignore")
                           + marker_bytes.decode())
            truncated = True
        total_diff_bytes += len(patch_slice.encode("utf-8", errors="replace"))
        files.append({
            "path": path,
            "status": status,
            "patch": patch_slice,
            "truncated": truncated,
        })
    return {
        "session_id": workspace.session_id,
        "branch": workspace.branch,
        "base_sha": workspace.base_sha,
        "files": files,
        "omitted_files": omitted,
    }
