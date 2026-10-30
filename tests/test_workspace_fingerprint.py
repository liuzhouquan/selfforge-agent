"""Workspace snapshots must be complete, bounded and confined to file descriptors."""

import os
import threading
import time

import pytest

from selfforge_agent.runtime import fingerprint
from selfforge_agent.runtime.fingerprint import WorkspaceSnapshotError, workspace_fingerprint


def test_snapshot_is_content_based_and_bound_to_workspace(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    path = root / "app.py"
    path.write_text("value = 1\n")
    original = workspace_fingerprint(root)
    os.utime(path, (1, 1))
    assert workspace_fingerprint(root) == original
    (root / "empty").mkdir()
    assert workspace_fingerprint(root) != original
    (root / "empty").rmdir()
    assert workspace_fingerprint(root) == original
    path.rename(root / "renamed.py")
    assert workspace_fingerprint(root) != original
    (root / "renamed.py").rename(path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "app.py").write_bytes(path.read_bytes())
    assert workspace_fingerprint(other) != original


def test_symlink_targets_are_recorded_without_reading_them(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "private"
    outside.write_text("first")
    link = root / "link"
    link.symlink_to(outside)
    original = workspace_fingerprint(root)
    outside.write_text("changed outside scope")
    assert workspace_fingerprint(root) == original
    link.unlink()
    link.symlink_to("missing")
    assert workspace_fingerprint(root) != original


def test_symlink_swap_does_not_open_outside_directory(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "child").mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret").write_text("PRIVATE_SENTINEL")
    real_open = os.open

    def swap(path, flags, *args, **kwargs):
        if path == "child":
            (root / "child").rmdir()
            (root / "child").symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises(WorkspaceSnapshotError, match="complete tree") as error:
        workspace_fingerprint(root)
    assert str(outside) not in str(error.value) and "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("limit", ["entries", "bytes", "depth"])
def test_limits_fail_closed(tmp_path, monkeypatch, limit):
    (tmp_path / "app.py").write_bytes(b"large")
    if limit == "entries":
        monkeypatch.setattr(fingerprint, "MAX_STAGED_ENTRIES", 0)
    elif limit == "bytes":
        monkeypatch.setattr(fingerprint, "MAX_STAGED_BYTES", 4)
    else:
        (tmp_path / "child").mkdir()
        monkeypatch.setattr(fingerprint, "MAX_SNAPSHOT_DEPTH", 0)
    with pytest.raises(WorkspaceSnapshotError, match="limit"):
        workspace_fingerprint(tmp_path)


def test_fifos_are_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(WorkspaceSnapshotError, match="unsupported"):
        workspace_fingerprint(tmp_path)


def test_cancellation_and_deadline_are_checked(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(KeyboardInterrupt):
        workspace_fingerprint(tmp_path, cancel=cancel)
    with pytest.raises(TimeoutError, match="fingerprint"):
        workspace_fingerprint(tmp_path, deadline=time.monotonic() - 1)


def test_private_and_generated_files_are_excluded(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    original = workspace_fingerprint(tmp_path)
    for name in (".git", ".selfforge", ".pytest_cache", ".ruff_cache", "__pycache__",
                 ".venv", "node_modules"):
        (tmp_path / name).mkdir()
        os.mkfifo(tmp_path / name / "must-not-open")
    (tmp_path / ".env.production").write_text("PRIVATE_SENTINEL")
    assert workspace_fingerprint(tmp_path) == original
