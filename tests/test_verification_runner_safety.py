import os
from pathlib import Path

import pytest

from selfforge_agent.verification.junit import parse_junit
from selfforge_agent.verification.runner import TestRunner, _read_junit_file, _test_target


def test_junit_parser_handles_deep_recursion_gracefully() -> None:
    # 3000 levels of nested tags triggers RecursionError in standard ElementTree
    deep_xml = b"<a>" * 3000 + b"</a>" * 3000
    evidence = parse_junit(deep_xml, False)
    assert evidence.error is not None
    assert "Invalid JUnit XML" in evidence.error


def test_junit_parser_rejects_excessive_test_counts() -> None:
    excessive_xml = b'<testsuite tests="100001" failures="0" errors="0" skipped="0"/>'
    evidence = parse_junit(excessive_xml, False)
    assert evidence.error is not None
    assert "limit" in evidence.error.lower() or "exceed" in evidence.error.lower()

    huge_xml = b'<testsuite tests="99999999999999999999999999999999999999999999999999"/>'
    evidence2 = parse_junit(huge_xml, False)
    assert evidence2.error is not None


def test_test_target_rejects_non_finite_and_out_of_bounds_timeouts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_ok(): pass\n")

    for invalid_timeout in (float("nan"), float("inf"), float("-inf"), 0, -5.0, 3601.0):
        with pytest.raises(ValueError, match="timeout must be a finite positive number not exceeding 3600 seconds"):
            _test_target(repo, "tests", invalid_timeout)


def test_test_target_rejects_missing_or_non_dir_repository_and_blank_test_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing_repo"
    with pytest.raises(ValueError, match="repository must be an existing directory"):
        _test_target(missing, "tests", 60.0)

    file_repo = tmp_path / "file_repo"
    file_repo.write_text("not a dir")
    with pytest.raises(ValueError, match="repository must be an existing directory"):
        _test_target(file_repo, "tests", 60.0)

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="test_path must not be blank"):
        _test_target(repo, "   ", 60.0)

    with pytest.raises(ValueError, match="traverses protected or invalid path component"):
        _test_target(repo, ".git/config", 60.0)


def test_test_target_rejects_non_regular_test_files_such_as_fifos(tmp_path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "mkfifo"):
        pytest.skip("POSIX mkfifo required")

    repo = tmp_path / "repo"
    repo.mkdir()
    fifo_path = repo / "tests_fifo.py"
    os.mkfifo(fifo_path)

    try:
        with pytest.raises(ValueError, match="test_path must be a regular file or directory"):
            _test_target(repo, "tests_fifo.py", 60.0)

        runner = TestRunner()
        with pytest.raises(ValueError, match="test_path must be a regular file or directory"):
            runner.run(repo, trusted=True, test_path="tests_fifo.py", timeout=5.0)
    finally:
        fifo_path.unlink(missing_ok=True)


def test_read_junit_file_rejects_symlinks_fifos_and_directories(tmp_path: Path) -> None:
    regular = tmp_path / "valid.xml"
    regular.write_text("<testsuite/>", encoding="utf-8")
    assert _read_junit_file(regular) == b"<testsuite/>"

    symlink = tmp_path / "link.xml"
    symlink.symlink_to(regular)
    assert _read_junit_file(symlink) is None

    broken_symlink = tmp_path / "broken_link.xml"
    broken_symlink.symlink_to(tmp_path / "nonexistent.xml")
    assert _read_junit_file(broken_symlink) is None

    directory = tmp_path / "somedir"
    directory.mkdir()
    assert _read_junit_file(directory) is None

    assert _read_junit_file(tmp_path / "missing.xml") is None

    if os.name == "posix" and hasattr(os, "mkfifo"):
        fifo_xml = tmp_path / "fifo.xml"
        os.mkfifo(fifo_xml)
        try:
            assert _read_junit_file(fifo_xml) is None
        finally:
            fifo_xml.unlink(missing_ok=True)


@pytest.mark.parametrize("protected", [".selfforge", ".env", ".env.private"])
def test_runner_rejects_alias_into_protected_directory(tmp_path: Path, protected: str) -> None:
    secret = tmp_path / protected
    secret.mkdir()
    (secret / "test_secret.py").write_text("def test_secret(): pass\n")
    (tmp_path / "public").symlink_to(secret, target_is_directory=True)
    with pytest.raises(ValueError, match="protected"):
        TestRunner().run(tmp_path, trusted=True, test_path="public/test_secret.py", timeout=5)


def test_junit_total_count_is_bounded_across_suites() -> None:
    evidence = parse_junit(
        b'<testsuites><testsuite tests="100000"/><testsuite tests="100000"/></testsuites>', False,
    )
    assert evidence.error is not None
    assert evidence.counts["passed"] == 0
