"""Pytest evidence from explicitly trusted host or isolated Docker execution."""

import math
import os
import platform
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.execution import DockerExecutor
from selfforge_agent.verification.junit import MAX_JUNIT_BYTES, parse_junit

PROTECTED_TARGETS = {
    ".git", ".venv", ".env", ".local", ".selfforge", ".workspaces", ".aws", ".ssh",
    ".npmrc", ".pypirc", ".netrc",
}


def _is_protected(name: str) -> bool:
    return name in PROTECTED_TARGETS or name.startswith(".env.")


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
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
        raise ValueError("timeout must be a finite positive number not exceeding 3600 seconds")
    if not repository.exists() or not repository.is_dir():
        raise ValueError("repository must be an existing directory")
    repo = repository.resolve()
    if not repo.is_dir():
        raise ValueError("repository must be an existing directory")
    if not isinstance(test_path, str) or not test_path.strip():
        raise ValueError("test_path must not be blank")
    for part in Path(test_path).parts:
        if part == ".." or _is_protected(part):
            raise ValueError(f"test_path traverses protected or invalid path component: {part}")
    target = (repo / test_path).resolve()
    if not target.is_relative_to(repo) or not target.exists():
        raise ValueError("test_path must exist inside the repository")
    if not (target.is_file() or target.is_dir()):
        raise ValueError("test_path must be a regular file or directory")
    return repo, target


def _read_junit_file(xml_path: Path) -> bytes | None:
    if not xml_path.is_file() or xml_path.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(xml_path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None
            with open(fd, "rb", closefd=True) as handle:
                fd = -1
                return handle.read(MAX_JUNIT_BYTES + 1)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError:
        return None


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
            xml = _read_junit_file(xml_path)
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
