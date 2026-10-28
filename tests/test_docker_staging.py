import tarfile
import time
from pathlib import Path
from threading import Event

import pytest

from selfforge_agent.runtime import execution
from selfforge_agent.runtime.execution import DockerExecutor


def _forbid_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        pytest.fail("Docker must not start when staging fails")

    monkeypatch.setattr(execution, "_run", fail)
    monkeypatch.setattr(execution.subprocess, "run", fail)


@pytest.mark.parametrize("limit,content,error", [
    ("MAX_STAGED_BYTES", b"x" * 32, "byte"),
    ("MAX_STAGED_ENTRIES", b"x", "entry"),
])
def test_staging_rejects_limits_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              limit: str, content: bytes, error: str) -> None:
    (tmp_path / "first.py").write_bytes(content)
    if limit == "MAX_STAGED_ENTRIES":
        (tmp_path / "second.py").write_bytes(content)
    monkeypatch.setattr(execution, limit, 1, raising=False)
    _forbid_docker(monkeypatch)

    result = DockerExecutor(tmp_path).run("true", 5)
    assert result.returncode != 0 and not result.timed_out
    assert error in result.output


def test_staging_deadline_expires_before_docker(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    _forbid_docker(monkeypatch)

    result = DockerExecutor(tmp_path).run("true", 0.000001)
    assert result.timed_out
    assert "staging" in result.output.lower()


def test_staging_rejects_archive_size_before_docker(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_bytes(b"x" * 1024)
    monkeypatch.setattr(execution, "MAX_ARCHIVE_BYTES", 512)
    _forbid_docker(monkeypatch)

    result = DockerExecutor(tmp_path).run("true", 5)
    assert result.returncode != 0 and not result.timed_out
    assert "archive byte limit" in result.output


def test_archive_deadline_cleans_temporary_files(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_bytes(b"x" * 1024)
    original_addfile = tarfile.TarFile.addfile
    original_tempdir = execution.tempfile.TemporaryDirectory
    temporary_paths: list[Path] = []

    def slow_addfile(self, tarinfo, fileobj=None):
        if tarinfo.isfile():
            time.sleep(0.03)
        return original_addfile(self, tarinfo, fileobj)

    def recorded_tempdir(*args, **kwargs):
        directory = original_tempdir(*args, **kwargs)
        temporary_paths.append(Path(directory.name))
        return directory

    monkeypatch.setattr(tarfile.TarFile, "addfile", slow_addfile)
    monkeypatch.setattr(execution.tempfile, "TemporaryDirectory", recorded_tempdir)
    _forbid_docker(monkeypatch)

    result = DockerExecutor(tmp_path).run("true", 0.01)
    assert result.timed_out
    assert temporary_paths and all(not path.exists() for path in temporary_paths)


def test_cancellation_during_archive_skips_docker(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_bytes(b"x" * 1024)
    cancel = Event()
    original_addfile = tarfile.TarFile.addfile

    def cancel_addfile(self, tarinfo, fileobj=None):
        if tarinfo.isfile():
            cancel.set()
        return original_addfile(self, tarinfo, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", cancel_addfile)
    _forbid_docker(monkeypatch)

    result = DockerExecutor(tmp_path, cancel=cancel).run("true", 5)
    assert result.returncode is None and not result.timed_out
    assert "cancelled" in result.output


def test_cancellation_during_copy_skips_docker(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_bytes(b"x" * (128 * 1024))

    class CancelDuringCopy(Event):
        checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            return self.checks >= 4

    _forbid_docker(monkeypatch)
    result = DockerExecutor(tmp_path, cancel=CancelDuringCopy()).run("true", 5)
    assert result.returncode is None and "cancelled" in result.output


def test_staging_preserves_symlinks_without_copying_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("private data", encoding="utf-8")
    (workspace / "shortcut").symlink_to(outside)
    (workspace / "app.py").write_text("print(1)\n", encoding="utf-8")
    (workspace / ".env").write_text("PRIVATE=secret", encoding="utf-8")
    staged = tmp_path / "staged"
    archive = tmp_path / "workspace.tar"
    deadline = time.monotonic() + 5

    execution._stage_workspace(workspace, staged, deadline, None)
    execution._archive_workspace(staged, archive, deadline, None)
    with tarfile.open(archive) as bundle:
        assert sorted(bundle.getnames()) == ["app.py", "shortcut"]
        assert bundle.getmember("shortcut").issym()
        assert bundle.extractfile("app.py").read() == b"print(1)\n"
