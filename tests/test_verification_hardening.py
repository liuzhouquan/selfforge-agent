"""Tests verifying security hardening of verification runner and JUnit parser (SF-VERIF-02)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import selfforge_agent.verification.junit as junit_mod
import selfforge_agent.verification.runner as runner_mod
from selfforge_agent.verification.junit import parse_junit
from selfforge_agent.verification.runner import (
    DockerTestRunner,
    TestRunner,
    _test_target,
)


class TestJUnitSecurityAndParsingHardening:
    """Verifies DTD/entity case sensitivity, testsuite ceilings, and failure parsing."""

    def test_case_insensitive_doctype_and_entity_rejected(self) -> None:
        """XML with lowercase or mixed-case DOCTYPE or ENTITY must be rejected."""
        lowercase_doctype = b'<!doctype test [ <!element test ANY> ]><testsuite tests="0"/>'
        evidence1 = parse_junit(lowercase_doctype, False)
        assert evidence1.error is not None
        assert "Invalid JUnit XML" in evidence1.error

        mixed_entity = b'<?xml version="1.0"?><!DocType test [ <!entity foo "bar"> ]><testsuite tests="0"/>'
        evidence2 = parse_junit(mixed_entity, False)
        assert evidence2.error is not None
        assert "Invalid JUnit XML" in evidence2.error

    def test_excessive_testsuite_count_rejected(self) -> None:
        """Exceeding MAX_TEST_SUITES limit must be rejected fail-closed."""
        max_suites = getattr(junit_mod, "MAX_TEST_SUITES", 1_000)
        suites = b"".join(b'<testsuite tests="0"/>' for _ in range(max_suites + 1))
        xml = b"<testsuites>" + suites + b"</testsuites>"
        evidence = parse_junit(xml, False)
        assert evidence.error is not None
        assert "Testsuite count exceeds limit" in evidence.error or "exceeds" in evidence.error.lower()

    def test_child_itertext_extracts_nested_failure_detail(self) -> None:
        """Failure detail extraction must preserve text across nested child elements."""
        xml = (
            b'<testsuite tests="1" failures="1" errors="0" skipped="0">'
            b'  <testcase classname="test_pkg" name="test_func">'
            b'    <failure message="AssertionError">Traceback:\n<frame>in file.py</frame>\nassert False</failure>'
            b"  </testcase>"
            b"</testsuite>"
        )
        evidence = parse_junit(xml, False)
        assert evidence.error is None
        assert len(evidence.failures) == 1
        assert "in file.py" in evidence.failures[0].detail
        assert "assert False" in evidence.failures[0].detail

    def test_whitespace_only_test_id_falls_back_to_unknown(self) -> None:
        """Blank or whitespace-only classname/name must safely fallback to 'unknown'."""
        xml = (
            b'<testsuite tests="1" failures="1" errors="0" skipped="0">'
            b'  <testcase classname="   " name="   ">'
            b'    <failure message="failed">something went wrong</failure>'
            b"  </testcase>"
            b"</testsuite>"
        )
        evidence = parse_junit(xml, False)
        assert evidence.error is None
        assert len(evidence.failures) == 1
        assert evidence.failures[0].test_id == "unknown"

    def test_inconsistent_testcase_count_vs_total_rejected(self) -> None:
        """When testcase elements exceed the suite's declared test count, fail-closed."""
        xml = (
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'  <testcase name="test_one"/>'
            b'  <testcase name="test_two"/>'
            b"</testsuite>"
        )
        evidence = parse_junit(xml, False)
        assert evidence.error is not None
        assert "Inconsistent testcase count" in evidence.error


class TestRunnerSubprocessAndTargetHardening:
    """Verifies target validation, symlink loops, and subprocess timeout race conditions."""

    def test_test_target_rejects_symlink_loop_with_value_error(self, tmp_path: Path) -> None:
        """Symlink loops must be trapped and mapped to ValueError, never leaking unhandled OSError."""
        repo = tmp_path / "repo"
        repo.mkdir()
        loop = repo / "loop.py"
        loop.symlink_to(loop)

        with pytest.raises(ValueError, match="Failed to resolve test_path|symlink|protected"):
            _test_target(repo, "loop.py", 60.0)

    def test_test_target_rejects_absolute_path_and_null_bytes(self, tmp_path: Path) -> None:
        """Absolute test_path or embedded null bytes must be rejected fail-closed."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(ValueError, match="relative path|traverses protected|inside the repository"):
            _test_target(repo, "/etc/passwd", 60.0)

        with pytest.raises(ValueError, match="null byte|protected|blank"):
            _test_target(repo, "tests\x00something.py", 60.0)

    def test_test_target_rejects_excessive_test_path_length(self, tmp_path: Path) -> None:
        """Path exceeding length limit (4096) must be rejected."""
        repo = tmp_path / "repo"
        repo.mkdir()
        huge_path = "a" * 4097

        with pytest.raises(ValueError, match="maximum length|invalid"):
            _test_target(repo, huge_path, 60.0)

    def test_runner_process_lookup_error_on_timeout_handled_gracefully(self, tmp_path: Path) -> None:
        """When process terminates right as timeout fires, ProcessLookupError must not crash runner."""
        repo = tmp_path / "repo"
        repo.mkdir()
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_dummy.py").write_text("def test_ok(): pass\n")

        runner = TestRunner()

        fake_process = MagicMock()
        fake_process.pid = 99999
        fake_process.returncode = None
        fake_process.communicate.side_effect = [
            pytest.importorskip("subprocess").TimeoutExpired(cmd=["pytest"], timeout=0.1),
            (b"", b""),
        ]

        with (
            patch("selfforge_agent.verification.runner.platform.platform", return_value="Linux-test"),
            patch("selfforge_agent.verification.runner.subprocess.Popen", return_value=fake_process),
            patch("selfforge_agent.verification.runner.os.killpg", side_effect=ProcessLookupError("No such process")),
        ):
            result = runner.run(repo, trusted=True, timeout=1.0)
            assert result.status == "timeout"
            assert "exceeded" in (result.error_message or "")


class TestKeywordAndDockerRunnerHardening:
    """Verifies keyword option injection defense and Docker runner image validation."""

    def test_keyword_validation_rejects_leading_dash_and_option_injection(self, tmp_path: Path) -> None:
        """Keyword starting with dash (pytest option injection) must be rejected."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("def test_ok(): pass\n")

        validate_kw = getattr(runner_mod, "_validate_keyword", None)
        runner = TestRunner()
        for bad_kw in ("-v", "-s", "--override-ini=addopts=-v", "-k something", "--collect-only"):
            if validate_kw is not None:
                with pytest.raises(ValueError, match="Test keyword must not start with '-'|Invalid test keyword"):
                    validate_kw(bad_kw)
            with pytest.raises(ValueError, match="Test keyword must not start with '-'|Invalid test keyword"):
                runner.run(repo, trusted=True, keyword=bad_kw)

    def test_keyword_validation_rejects_null_bytes_and_control_chars(self, tmp_path: Path) -> None:
        """Keyword containing null bytes, newlines, or control chars must be rejected."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("def test_ok(): pass\n")

        validate_kw = getattr(runner_mod, "_validate_keyword", None)
        runner = TestRunner()
        for bad_kw in ("test\x00foo", "test\nbar", "test\r\nbaz", "a" * 513):
            if validate_kw is not None:
                with pytest.raises(ValueError, match="Invalid test keyword|null byte|length"):
                    validate_kw(bad_kw)
            with pytest.raises(ValueError, match="Invalid test keyword|null byte|length"):
                runner.run(repo, trusted=True, keyword=bad_kw)

    def test_docker_test_runner_rejects_invalid_image(self) -> None:
        """DockerTestRunner must validate image name fail-closed at construction."""
        for bad_img in ("", "   ", "image with space", "image\x00null", "image\ninjection", "a" * 257):
            with pytest.raises(ValueError, match="image|Docker image"):
                DockerTestRunner(image=bad_img)


def test_docker_runner_accepts_immutable_named_digest():
    image = "registry.example/team/python@sha256:" + "a" * 64
    assert DockerTestRunner(image=image).image == image


def test_docker_runner_rejects_leading_option_at_construction():
    with pytest.raises(ValueError, match="image"):
        DockerTestRunner(image="--privileged")


@pytest.mark.parametrize("runner_class", [TestRunner, DockerTestRunner])
@pytest.mark.parametrize("timeout", [10**400, True, "60"], ids=["huge_integer", "boolean", "string"])
def test_runner_rejects_invalid_timeout_before_execution(tmp_path, monkeypatch, runner_class, timeout):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok(): pass\n")

    def unexpected(*args, **kwargs):
        pytest.fail("invalid timeout reached process or Docker construction")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", unexpected)
    monkeypatch.setattr(runner_mod, "DockerExecutor", unexpected)
    with pytest.raises(ValueError, match="timeout"):
        runner_class().run(tmp_path, trusted=True, timeout=timeout)
