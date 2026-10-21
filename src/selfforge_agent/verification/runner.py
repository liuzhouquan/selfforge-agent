"""Pytest evidence from explicitly trusted host or isolated Docker execution."""

import os
import platform
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import DockerExecutor


class TrustRequiredError(ValueError):
    """Running repository code requires an explicit trust choice."""


def _junit_counts(xml: bytes | None, timed_out: bool) -> tuple[dict[str, int], str | None]:
    counts = {key: 0 for key in ("passed", "failed", "errors", "skipped")}
    if xml is None:
        return counts, None if timed_out else "pytest did not produce JUnit XML"
    try:
        root = ET.fromstring(xml)
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites:
            raise ValueError("No testsuite element")
        total = sum(int(s.get("tests", "0")) for s in suites)
        counts["failed"] = sum(int(s.get("failures", "0")) for s in suites)
        counts["errors"] = sum(int(s.get("errors", "0")) for s in suites)
        counts["skipped"] = sum(int(s.get("skipped", "0")) for s in suites)
        counts["passed"] = max(0, total - counts["failed"] - counts["errors"] - counts["skipped"])
        return counts, None
    except (ET.ParseError, ValueError) as exc:
        return counts, f"Invalid JUnit XML: {exc}"


def _result_status(returncode: int | None, timed_out: bool, counts: dict[str, int],
                   parse_error: str | None, timeout: float) -> tuple[str, str | None]:
    if timed_out:
        return "timeout", f"pytest exceeded {timeout:g} seconds"
    if parse_error:
        return "error", parse_error
    if returncode == 0:
        return "passed", None
    if counts["failed"] or counts["errors"]:
        return "failed", None
    return "error", f"pytest exited with code {returncode}"


def _test_target(repository: Path, test_path: str, timeout: float) -> tuple[Path, Path]:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    repo = repository.resolve()
    target = (repo / test_path).resolve()
    if not target.is_relative_to(repo) or not target.exists():
        raise ValueError("test_path must exist inside the repository")
    return repo, target


class TestRunner:
    requires_trust = True

    def run(
        self,
        repository: Path,
        *,
        trusted: bool = False,
        timeout: float = 60,
        test_path: str = "tests",
    ) -> TestResult:
        if not trusted:
            raise TrustRequiredError("Pass trusted=True only for a repository you trust")
        repo, target = _test_target(repository, test_path, timeout)
        environment = f"local; python={sys.version.split()[0]}; platform={platform.platform()}"
        with tempfile.TemporaryDirectory(prefix="selfforge-agent-") as tempdir:
            xml_path = Path(tempdir) / "junit.xml"
            command = [sys.executable, "-m", "pytest", str(target), f"--junitxml={xml_path}"]
            start = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                stdout, stderr = process.communicate()
            duration = time.monotonic() - start
            counts, parse_error = _junit_counts(xml_path.read_bytes() if xml_path.exists() else None,
                                                timed_out)
            status, error = _result_status(process.returncode, timed_out, counts,
                                           parse_error, timeout)
            return TestResult(
                command=command,
                environment=environment,
                exit_code=None if timed_out else process.returncode,
                duration_seconds=duration,
                status=status,
                stdout=stdout.decode("utf-8", "replace")[-20000:],
                stderr=stderr.decode("utf-8", "replace")[-20000:],
                error_message=error,
                **counts,
            )


class DockerTestRunner:
    """Run each worktree's tests in a discarded copy and parse its JUnit artifact."""

    requires_trust = False

    def __init__(self, image: str = "selfforge-agent-sandbox:latest") -> None:
        self.image = image

    def run(self, repository: Path, *, trusted: bool = False, timeout: float = 60,
            test_path: str = "tests") -> TestResult:
        repo, target = _test_target(repository, test_path, timeout)
        relative = target.relative_to(repo).as_posix()
        command = ["python", "-m", "pytest", relative, "--junitxml=/artifacts/junit.xml"]
        start = time.monotonic()
        outcome, xml = DockerExecutor(repo, image=self.image).run_with_artifact(
            shlex.join(command), timeout, "junit.xml")
        duration = time.monotonic() - start
        counts, parse_error = _junit_counts(xml, outcome.timed_out)
        status, error = _result_status(outcome.returncode, outcome.timed_out,
                                       counts, parse_error, timeout)
        if status == "error" and parse_error and outcome.output:
            error = f"{parse_error}: {outcome.output[-500:]}"
        return TestResult(
            command=command, environment=f"docker; image={self.image}; network=none",
            exit_code=outcome.returncode, duration_seconds=duration,
            status=status, stdout=outcome.output[-20000:], stderr="",
            error_message=error, **counts,
        )
