"""Security and robustness tests for runtime execution and failure artifacts (SF-RUN-08)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor, resolve_docker_image
from selfforge_agent.runtime.failure_artifacts import (
    prepare_artifacts,
    read_failure,
    write_failure,
)

TestResult.__test__ = False


def test_write_failure_rejects_path_traversal_in_case_name(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    for bad_case in ("../../escape", "../bad", "sub/dir", "", "a" * 129, "case with spaces", "bad!name"):
        with pytest.raises(ValueError, match="Invalid case name"):
            write_failure(root, case=bad_case, repeat=0, reason="failed", visible=None, holdout=None)


def test_write_failure_rejects_invalid_repeat(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    for bad_repeat in (-1, float("nan"), float("inf"), True, False, 1.5, 100_001):
        with pytest.raises(ValueError, match="repeat"):
            write_failure(root, case="addition", repeat=bad_repeat, reason="failed", visible=None, holdout=None)  # type: ignore[arg-type]


def test_write_failure_rejects_symlink_root_and_target(tmp_path: Path) -> None:
    # 1. Root is symlink
    real_root = tmp_path / "real_artifacts"
    real_root.mkdir()
    sym_root = tmp_path / "sym_artifacts"
    sym_root.symlink_to(real_root)

    with pytest.raises(ValueError, match="non-symlink"):
        write_failure(sym_root, case="addition", repeat=0, reason="failed", visible=None, holdout=None)

    # 2. Target is symlink
    victim = tmp_path / "victim.txt"
    victim.write_text("initial", encoding="utf-8")
    target = real_root / "addition-0.json"
    target.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        write_failure(real_root, case="addition", repeat=0, reason="failed", visible=None, holdout=None)

    assert victim.read_text(encoding="utf-8") == "initial"


def test_prepare_artifacts_rejects_symlinks_and_repo_conflicts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # Inside repo
    with pytest.raises(ValueError, match="stay outside"):
        prepare_artifacts(repo / "artifacts", [repo])

    # Directory is symlink
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(real_dir)

    with pytest.raises(ValueError, match="symlink"):
        prepare_artifacts(sym_dir, [repo])


def test_read_failure_safely_reads_valid_artifact(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    res = TestResult(
        command=["pytest"],
        environment="test-env",
        exit_code=1,
        duration_seconds=1.2,
        passed=0,
        failed=1,
        errors=0,
        skipped=0,
        status="failed",
        stdout="FAIL",
        stderr="ERR",
    )
    filename = write_failure(root, case="addition", repeat=1, reason="assertion error", visible=res, holdout=None)

    payload = read_failure(root, filename)
    assert payload["version"] == 1
    assert payload["case"] == "addition"
    assert payload["repeat"] == 1
    assert payload["reason"] == "assertion error"
    assert payload["visible"]["exit_code"] == 1


def test_read_failure_rejects_symlinks_fifos_and_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    # 1. Invalid filename
    for bad_name in ("../escape-0.json", "bad", "case-0.txt", "case-1.json/sub"):
        with pytest.raises(ValueError, match="filename|traversal"):
            read_failure(root, bad_name)

    # 2. Symlink
    victim = tmp_path / "victim.json"
    victim.write_text('{"version": 1}', encoding="utf-8")
    sym_file = root / "addition-0.json"
    sym_file.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        read_failure(root, "addition-0.json")

    sym_file.unlink()

    # 3. FIFO
    os.mkfifo(sym_file)
    with pytest.raises(ValueError, match="regular file"):
        read_failure(root, "addition-0.json")

    sym_file.unlink()

    # 4. Corrupt JSON
    sym_file.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt artifact"):
        read_failure(root, "addition-0.json")


def test_executor_rejects_non_finite_and_non_positive_timeout(tmp_path: Path) -> None:
    host = HostExecutor(tmp_path, trusted=True)
    docker = DockerExecutor(tmp_path)

    for bad_timeout in (0, -1, float("nan"), float("inf"), -0.0):
        with pytest.raises(ValueError, match="timeout"):
            host.run("true", bad_timeout)

        with pytest.raises(ValueError, match="timeout"):
            docker.run("true", bad_timeout)


def test_executor_rejects_empty_and_null_byte_commands(tmp_path: Path) -> None:
    host = HostExecutor(tmp_path, trusted=True)
    docker = DockerExecutor(tmp_path)

    for bad_cmd in ("", "   ", "\n", "echo hello\x00world"):
        with pytest.raises(ValueError, match="Command"):
            host.run(bad_cmd, 5)

        with pytest.raises(ValueError, match="Command"):
            docker.run(bad_cmd, 5)


def test_executor_rejects_symlink_or_missing_root(tmp_path: Path) -> None:
    # Missing directory
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="existing non-symlink"):
        HostExecutor(missing, trusted=True)

    with pytest.raises(ValueError, match="existing non-symlink"):
        DockerExecutor(missing)

    # Symlink directory
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(real_dir)

    with pytest.raises(ValueError, match="existing non-symlink"):
        HostExecutor(sym_dir, trusted=True)

    with pytest.raises(ValueError, match="existing non-symlink"):
        DockerExecutor(sym_dir)


def test_docker_artifact_name_validation(tmp_path: Path) -> None:
    docker = DockerExecutor(tmp_path)

    for bad_name in ("../../escape", "sub/dir", "name\x00", "", ".", "..", "x" * 257):
        with pytest.raises(ValueError, match="Artifact name"):
            docker.run_with_artifact("true", 5, bad_name)


def test_resolve_docker_image_rejects_non_finite_timeout() -> None:
    for bad_timeout in (0, -1, float("nan"), float("inf")):
        with pytest.raises(TimeoutError):
            resolve_docker_image("ubuntu:latest", timeout=bad_timeout)
