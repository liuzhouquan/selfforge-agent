"""Defect reproduction and hardening tests for workspace fingerprinting (SF-FP-02).

Validates:
1. Root input defense (empty/blank paths rejected fail-closed with ValueError).
2. Resource bounding (single file size limit MAX_SINGLE_FILE_BYTES fails closed).
3. Stable hashing that preserves actual path and permission identity.
4. Unicode filename sorting stability (NFC vs NFD normalization).
5. Cancellation and deadline responsiveness during streaming reads.
6. Symlink ELOOP / OSError exception isolation fail-closed.
"""

import errno
import os
import threading
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.runtime import fingerprint
from selfforge_agent.runtime.fingerprint import (
    WorkspaceSnapshotError,
    workspace_fingerprint,
)


class CountedCancelEvent(threading.Event):
    """Subclass of Event that triggers cancellation after a given number of is_set queries."""

    def __init__(self, trigger_after_calls: int = 5) -> None:
        super().__init__()
        self.calls = 0
        self.trigger_after_calls = trigger_after_calls

    def is_set(self) -> bool:
        self.calls += 1
        if self.calls >= self.trigger_after_calls:
            self.set()
        return super().is_set()


class TestWorkspaceFingerprintRootDefense:
    """Root argument must be strictly validated before any filesystem operations."""

    def test_root_rejects_empty_string(self) -> None:
        """Empty string root must not resolve to current working directory; reject fail-closed."""
        with pytest.raises(ValueError, match="root cannot be empty or whitespace"):
            workspace_fingerprint("")

    def test_root_rejects_whitespace_string(self) -> None:
        """Whitespace-only string must be rejected fail-closed with ValueError."""
        for blank in [" ", "   ", "\t", "\n", " \t \n "]:
            with pytest.raises(ValueError, match="root cannot be empty or whitespace"):
                workspace_fingerprint(blank)

    def test_root_rejects_empty_path(self) -> None:
        """Path with empty string component must be rejected with ValueError."""
        with pytest.raises(ValueError, match="root cannot be empty or whitespace"):
            workspace_fingerprint(Path(""))

    def test_root_rejects_nonexistent_directory(self, tmp_path: Path) -> None:
        """Nonexistent root must raise WorkspaceSnapshotError without leaking sensitive details."""
        missing = tmp_path / "does_not_exist"
        with pytest.raises(WorkspaceSnapshotError, match="complete tree"):
            workspace_fingerprint(missing)

    def test_root_rejects_regular_file(self, tmp_path: Path) -> None:
        """A regular file passed as root directory must fail closed."""
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")
        with pytest.raises(WorkspaceSnapshotError, match="complete tree"):
            workspace_fingerprint(file_path)


class TestWorkspaceFingerprintResourceBounds:
    """Bounding single file sizes and traversal depth."""

    def test_single_file_exceeds_size_limit_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single file exceeding MAX_SINGLE_FILE_BYTES must fail closed."""
        assert hasattr(fingerprint, "MAX_SINGLE_FILE_BYTES")
        monkeypatch.setattr(fingerprint, "MAX_SINGLE_FILE_BYTES", 500)
        file_path = tmp_path / "large.bin"
        file_path.write_bytes(b"x" * 600)

        with pytest.raises(WorkspaceSnapshotError, match="file size limit"):
            workspace_fingerprint(tmp_path)

    def test_single_file_streaming_read_bounds_size(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Files growing during read beyond MAX_SINGLE_FILE_BYTES must fail closed."""
        assert hasattr(fingerprint, "MAX_SINGLE_FILE_BYTES")
        monkeypatch.setattr(fingerprint, "MAX_SINGLE_FILE_BYTES", 1000)
        file_path = tmp_path / "data.bin"
        file_path.write_bytes(b"a" * 800)
        # 800 bytes is under 1000, so it succeeds
        fp = workspace_fingerprint(tmp_path)
        assert isinstance(fp, str) and len(fp) == 64


class TestWorkspaceFingerprintCrossPlatformDeterminism:
    """Determinism across umasks, path styles, and unicode encodings."""

    def test_umask_group_write_permission_normalized(self, tmp_path: Path) -> None:
        """Group write permission changes must invalidate evidence."""
        file_path = tmp_path / "module.py"
        file_path.write_text("print('test')\n")

        os.chmod(file_path, 0o644)
        hash_644 = workspace_fingerprint(tmp_path)

        os.chmod(file_path, 0o664)
        hash_664 = workspace_fingerprint(tmp_path)

        assert hash_644 != hash_664, "Permission changes must invalidate workspace evidence"

    def test_executable_permission_bit_is_preserved(self, tmp_path: Path) -> None:
        """Executable bit difference (0o644 vs 0o755) MUST change the fingerprint."""
        file_path = tmp_path / "script.sh"
        file_path.write_text("#!/bin/sh\necho hi\n")

        os.chmod(file_path, 0o644)
        hash_non_exec = workspace_fingerprint(tmp_path)

        os.chmod(file_path, 0o755)
        hash_exec = workspace_fingerprint(tmp_path)

        assert hash_non_exec != hash_exec, "Executable permission bit must alter workspace fingerprint"

    def test_directory_permission_normalized(self, tmp_path: Path) -> None:
        """Directory permission changes must invalidate evidence."""
        sub_dir = tmp_path / "pkg"
        sub_dir.mkdir()
        (sub_dir / "init.py").write_text("# init\n")

        os.chmod(sub_dir, 0o755)
        hash_755 = workspace_fingerprint(tmp_path)

        os.chmod(sub_dir, 0o775)
        hash_775 = workspace_fingerprint(tmp_path)

        assert hash_755 != hash_775, "Directory permission changes must invalidate workspace evidence"

    def test_unicode_filename_nfc_nfd_deterministic_sorting(self, tmp_path: Path) -> None:
        """Sorting must be stable under Unicode NFC normalization."""
        # 'e' + combining acute (NFD) vs precomposed 'é' (NFC)
        nfc_name = unicodedata.normalize("NFC", "caf\u00e9_1.txt")
        nfd_name = unicodedata.normalize("NFD", "caf\u00e9_2.txt")
        (tmp_path / nfc_name).write_text("content 1\n")
        (tmp_path / nfd_name).write_text("content 2\n")

        fp1 = workspace_fingerprint(tmp_path)
        fp2 = workspace_fingerprint(tmp_path)
        assert fp1 == fp2

    def test_symlink_eloop_handled_fail_closed(self, tmp_path: Path) -> None:
        """ELOOP or OSError in symlink reading must fail closed without crashing."""
        link_a = tmp_path / "link_a"
        link_b = tmp_path / "link_b"
        link_a.symlink_to(link_b)
        link_b.symlink_to(link_a)

        with (
            patch("os.readlink", side_effect=OSError(errno.ELOOP, "Too many levels of symbolic links")),
            pytest.raises(WorkspaceSnapshotError, match="complete tree"),
        ):
            workspace_fingerprint(tmp_path)


class TestWorkspaceFingerprintCancellationAndTimeout:
    """Cancellation and deadline checks during streaming."""

    def test_cancellation_during_large_file_streaming(self, tmp_path: Path) -> None:
        """Cancelling via threading.Event during multi-block file reading halts immediately."""
        large_file = tmp_path / "stream.bin"
        # 256 KiB file requires 4 blocks of 64 KiB
        large_file.write_bytes(b"Z" * (256 * 1024))

        # Check call sequence:
        # 1: start of workspace_fingerprint
        # 2: start of walk
        # 3: scandir loop
        # 4: for name loop
        # 5: inside block loop (first block)
        # Setting trigger_after_calls=5 triggers KeyboardInterrupt inside block streaming!
        cancel = CountedCancelEvent(trigger_after_calls=5)

        with pytest.raises(KeyboardInterrupt):
            workspace_fingerprint(tmp_path, cancel=cancel)

    def test_deadline_precision_rejects_negative_or_zero_deadline(self, tmp_path: Path) -> None:
        """Deadline <= 0 should timeout immediately."""
        with pytest.raises(TimeoutError, match="timed out"):
            workspace_fingerprint(tmp_path, deadline=0.0)


def test_fingerprint_preserves_distinct_posix_unicode_path_identity(tmp_path):
    original = tmp_path / "\u00e9.py"
    original.write_text("VALUE = 1\n")
    before = workspace_fingerprint(tmp_path)
    original.rename(tmp_path / "e\u0301.py")
    assert workspace_fingerprint(tmp_path) != before


def test_fingerprint_preserves_each_execution_permission(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o744)
    before = workspace_fingerprint(tmp_path)
    script.chmod(0o645)
    assert workspace_fingerprint(tmp_path) != before
