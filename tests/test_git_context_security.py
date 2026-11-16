"""Security and input defense tests for Git context and diff parsing (SF-CORE-01)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure local worktree src is loaded first
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from selfforge_agent.core import git_context
from selfforge_agent.core.git_context import (
    GitContextError,
    build_context,
    parse_hunks,
)


def test_build_context_rejects_symlink_repository(demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    symlink_repo = repo.parent / "symlink_repo"
    os.symlink(repo, symlink_repo)

    with pytest.raises(GitContextError, match="symlink"):
        build_context(symlink_repo, base, head)


def test_build_context_rejects_truncated_and_malformed_diff_tokens(
    demo_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base, head = demo_repo
    orig_git = git_context._git

    # 1. Truncated rename tokens (R100 followed by only 1 filename instead of 2)
    def fake_git_rename(r: Path, *args: str) -> bytes:
        if "diff" in args and "--name-status" in args:
            return b"R100\0only_one_path.py\0"
        return orig_git(r, *args)

    monkeypatch.setattr(git_context, "_git", fake_git_rename)
    with pytest.raises(GitContextError, match="diff token stream"):
        build_context(repo, base, head)

    # 2. Truncated modified token (M with no filename)
    def fake_git_mod(r: Path, *args: str) -> bytes:
        if "diff" in args and "--name-status" in args:
            return b"M\0"
        return orig_git(r, *args)

    monkeypatch.setattr(git_context, "_git", fake_git_mod)
    with pytest.raises(GitContextError, match="diff token stream"):
        build_context(repo, base, head)


def test_build_context_rejects_path_traversal_in_diff_paths(
    demo_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base, head = demo_repo
    orig_git = git_context._git

    # Malicious changed file path in git diff
    for bad_path in (
        b"../../etc/passwd",
        b"/etc/shadow",
        b"app\x00.py",
        b"app\r\n.py",
        b"foo/../../bar.py",
    ):
        def fake_git(r: Path, *args: str, p=bad_path) -> bytes:
            if "diff" in args and "--name-status" in args:
                return b"M\0" + p + b"\0"
            return orig_git(r, *args)

        monkeypatch.setattr(git_context, "_git", fake_git)
        with pytest.raises(GitContextError):
            build_context(repo, base, head)


def test_parse_hunks_bounds_patch_size_and_line_numbers() -> None:
    # 1. Oversized patch exceeding 10 MiB
    large_patch = " " * (10 * 1024 * 1024 + 1)
    with pytest.raises(GitContextError, match="size"):
        parse_hunks(large_patch)

    # 2. Astronomical line number
    bad_hunk = "@@ -999999999999,1 +999999999999,1 @@\n+line"
    with pytest.raises(GitContextError, match="line number"):
        parse_hunks(bad_hunk)

    # 3. Excessive number of hunks
    hunks_stream = "@@ -1,1 +1,1 @@\n+line\n" * 10_001
    with pytest.raises(GitContextError, match="hunks"):
        parse_hunks(hunks_stream)


def test_git_execution_bounds_output_and_validates_timeout(
    demo_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = demo_repo

    # Use actual Git bytes at the bounded capture boundary.
    with pytest.raises(GitContextError, match="size"):
        git_context._git(repo, "show", "HEAD:calculator.py", max_output_bytes=4)

    # 2. Invalid timeouts
    for bad_timeout in (float("nan"), float("inf"), -1.0, 0, True, False, "30"):
        with pytest.raises(GitContextError, match="Timeout"):
            git_context._git(repo, "status", timeout=bad_timeout)  # type: ignore[arg-type]
