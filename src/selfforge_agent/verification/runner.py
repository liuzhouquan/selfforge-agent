"""Pytest evidence from explicitly trusted host or isolated Docker execution."""

import os
import platform
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import DockerExecutor
from selfforge_agent.verification.junit import MAX_JUNIT_BYTES, parse_junit


class TrustRequiredError(ValueError):
    """Running repository code requires an explicit trust choice."""


def _result_status(returncode: int | None, timed_out: bool, counts: dict[str, int],
                   parse_error: str | None, timeout: float) -> tuple[str, str | None]:
    if timed_out:
        return "timeout", f"pytest exceeded {timeout:g} seconds"
    if parse_error:
        return "error", parse_error
    if counts["failed"] or counts["errors"]:
        return "failed", None
    if returncode == 0:
        if counts["passed"] > 0:
            return "passed", None
        return "error", "pytest produced no passing tests (empty or all skipped)"
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
        keyword: str | None = None,
    ) -> TestResult:
        if not trusted:
            raise TrustRequiredError("Pass trusted=True only for a repository you trust")
        repo, target = _test_target(repository, test_path, timeout)
        environment = f"local; python={sys.version.split()[0]}; platform={platform.platform()}"
        with tempfile.TemporaryDirectory(prefix="selfforge-agent-") as tempdir:
            xml_path = Path(tempdir) / "junit.xml"
            # A fresh cache namespace avoids timestamp/size-valid .pyc files from
            # an earlier test run after a rapid same-size source edit.
            command = [sys.executable, "-B", "-X", f"pycache_prefix={Path(tempdir) / 'pycache'}",
                       "-m", "pytest", str(target)]
            if keyword is not None:
                if not keyword.strip():
                    raise ValueError("Test keyword must not be blank")
                command.extend(["-k", keyword])
            command.append(f"--junitxml={xml_path}")
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
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                timed_out = True
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                stdout, stderr = process.communicate()
                if isinstance(exc, KeyboardInterrupt):
                    raise
            duration = time.monotonic() - start
            xml = None
            if xml_path.exists():
                with xml_path.open("rb") as handle:
                    xml = handle.read(MAX_JUNIT_BYTES + 1)
            evidence = parse_junit(xml, timed_out)
            status, error = _result_status(process.returncode, timed_out, evidence.counts,
                                           evidence.error, timeout)
            return TestResult(
                command=command,
                environment=environment,
                exit_code=None if timed_out else process.returncode,
                duration_seconds=duration,
                status=status,
                stdout=stdout.decode("utf-8", "replace")[-20000:],
                stderr=stderr.decode("utf-8", "replace")[-20000:],
                error_message=error,
                failure_details=evidence.failures,
                failure_details_truncated=evidence.truncated,
                **evidence.counts,
            )


class DockerTestRunner:
    """Run each worktree's tests in a discarded copy and parse its JUnit artifact."""

    requires_trust = False

    def __init__(self, image: str = "selfforge-agent-sandbox:latest") -> None:
        self.image = image

    def run(self, repository: Path, *, trusted: bool = False, timeout: float = 60,
            test_path: str = "tests", keyword: str | None = None,
            executor: DockerExecutor | None = None) -> TestResult:
        repo, target = _test_target(repository, test_path, timeout)
        if executor is None:
            executor = DockerExecutor(repo, image=self.image)
        elif executor.root != repo or executor.image != self.image:
            raise ValueError("Docker executor workspace and image must match the test runner")
        relative = target.relative_to(repo).as_posix()
        command = ["python", "-m", "pytest", relative]
        if keyword is not None:
            if not keyword.strip():
                raise ValueError("Test keyword must not be blank")
            command.extend(["-k", keyword])
        command.append("--junitxml=/artifacts/junit.xml")
        start = time.monotonic()
        outcome, xml = executor.run_with_artifact(shlex.join(command), timeout, "junit.xml")
        duration = time.monotonic() - start
        evidence = parse_junit(xml, outcome.timed_out)
        status, error = _result_status(outcome.returncode, outcome.timed_out,
                                       evidence.counts, evidence.error, timeout)
        if status == "error" and evidence.error and outcome.output:
            error = f"{evidence.error}: {outcome.output[-500:]}"
        return TestResult(
            command=command, environment=f"docker; image={self.image}; network=none",
            exit_code=outcome.returncode, duration_seconds=duration,
            status=status, stdout=outcome.output[-20000:], stderr="",
            error_message=error, failure_details=evidence.failures,
            failure_details_truncated=evidence.truncated, **evidence.counts,
        )
