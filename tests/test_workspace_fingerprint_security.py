"""Security and boundary tests for workspace snapshot fingerprinting (SF-FPR-01)."""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.runtime.fingerprint import (
    WorkspaceSnapshotError,
    workspace_fingerprint,
)


def test_workspace_fingerprint_rejects_invalid_root_type() -> None:
    """Non-Path and non-str root must be rejected with TypeError (fail-closed)."""
    for invalid in [None, 123, [], {}, True, False]:
        with pytest.raises(TypeError, match="root must be a Path or str"):
            workspace_fingerprint(invalid)  # type: ignore[arg-type]


def test_workspace_fingerprint_supports_str_and_rejects_null_bytes(tmp_path: Path) -> None:
    """Passing root as a valid str should succeed; null bytes must be rejected with ValueError."""
    # str path should produce identical hash to Path
    fp_path = workspace_fingerprint(tmp_path)
    fp_str = workspace_fingerprint(str(tmp_path))
    assert fp_str == fp_path

    # Null byte in path must be rejected fail-closed
    with pytest.raises(ValueError, match="null byte"):
        workspace_fingerprint(str(tmp_path) + "\0bad")

    with pytest.raises(ValueError, match="null byte"):
        workspace_fingerprint(Path(str(tmp_path) + "\0bad"))


def test_workspace_fingerprint_rejects_symlink_root_even_with_trailing_slash(tmp_path: Path) -> None:
    """A symlink root must be rejected fail-closed even when specified with a trailing slash."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("content")

    sym_link = tmp_path / "sym_link"
    sym_link.symlink_to(real_dir, target_is_directory=True)

    # Bare symlink Path
    with pytest.raises(WorkspaceSnapshotError, match="symlink|complete tree"):
        workspace_fingerprint(sym_link)

    # Symlink Path with trailing slash (POSIX trailing slash traversal attempt)
    with pytest.raises(WorkspaceSnapshotError, match="symlink|complete tree"):
        workspace_fingerprint(Path(str(sym_link) + "/"))

    # Symlink str with trailing slash
    with pytest.raises(WorkspaceSnapshotError, match="symlink|complete tree"):
        workspace_fingerprint(str(sym_link) + "/")


def test_workspace_fingerprint_validates_deadline(tmp_path: Path) -> None:
    """deadline must be a finite number or None; NaN, infinity, booleans must fail-closed."""
    for bad_deadline in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValueError, match="deadline must be a finite number"):
            workspace_fingerprint(tmp_path, deadline=bad_deadline)

    for bad_type in [True, False, "10.0", [10.0]]:
        with pytest.raises(TypeError, match="deadline must be a float or int or None"):
            workspace_fingerprint(tmp_path, deadline=bad_type)  # type: ignore[arg-type]


def test_workspace_fingerprint_validates_cancel_type(tmp_path: Path) -> None:
    """cancel must be a threading.Event instance or None."""
    for bad_cancel in ["cancel", 123, True, [], {}]:
        with pytest.raises(TypeError, match="cancel must be an Event instance or None"):
            workspace_fingerprint(tmp_path, cancel=bad_cancel)  # type: ignore[arg-type]


def test_workspace_fingerprint_detects_directory_cycle(tmp_path: Path) -> None:
    """Directory cycle (e.g. recursive bind mount) must be detected and rejected."""
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()

    real_fstat = os.fstat
    real_stat = os.stat
    root_st = os.stat(tmp_path)

    def mock_fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        if stat.S_ISDIR(st.st_mode) and fd != 0:
            return os.stat_result((
                st.st_mode, root_st.st_ino, root_st.st_dev, st.st_nlink,
                st.st_uid, st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime,
            ))
        return st

    def mock_stat(path, *args, **kwargs) -> os.stat_result:
        st = real_stat(path, *args, **kwargs)
        if path == "sub" or (isinstance(path, Path) and path.name == "sub"):
            return os.stat_result((
                st.st_mode, root_st.st_ino, root_st.st_dev, st.st_nlink,
                st.st_uid, st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime,
            ))
        return st

    with (
        patch("os.fstat", side_effect=mock_fstat),
        patch("os.stat", side_effect=mock_stat),
        pytest.raises(WorkspaceSnapshotError, match="cycle"),
    ):
        workspace_fingerprint(tmp_path)


def test_workspace_fingerprint_rejects_negative_file_size(tmp_path: Path) -> None:
    """Files reporting negative st_size must be rejected fail-closed."""
    file = tmp_path / "corrupt.bin"
    file.write_bytes(b"data")

    real_stat = os.stat

    def mock_stat(*args, **kwargs) -> os.stat_result:
        st = real_stat(*args, **kwargs)
        if stat.S_ISREG(st.st_mode):
            return os.stat_result((
                st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                st.st_uid, st.st_gid, -100, st.st_atime, st.st_mtime, st.st_ctime,
            ))
        return st

    with patch("os.stat", side_effect=mock_stat), pytest.raises(WorkspaceSnapshotError, match="invalid file size"):
        workspace_fingerprint(tmp_path)


def test_root_replaced_during_scan_invalidates_fingerprint(tmp_path, monkeypatch):
    import selfforge_agent.runtime.fingerprint as module

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "old.txt").write_text("old")
    real_lstat = os.lstat
    calls = 0

    def replace_after_lstat(path, *args, **kwargs):
        nonlocal calls
        result = real_lstat(path, *args, **kwargs)
        if str(path) == str(root):
            calls += 1
        if str(path) == str(root) and calls == 3:
            root.rename(tmp_path / "previous")
            root.mkdir()
            (root / "new.txt").write_text("new")
        return result

    monkeypatch.setattr(module.os, "lstat", replace_after_lstat)
    with pytest.raises(WorkspaceSnapshotError, match="changed"):
        workspace_fingerprint(root)
