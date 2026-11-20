"""Pytest harness adapter supporting Host and isolated Docker execution."""

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

from selfforge_agent.runtime.execution import DockerExecutor
from selfforge_agent.verification.harness.base_adapter import BaseHarnessAdapter
from selfforge_agent.verification.harness.contracts import (
    PytestSelection,
    TestExecutionRequest,
    TestPlan,
    TestRunResult,
    compute_plan_digest,
    is_protected_name,
)
from selfforge_agent.verification.harness.evaluator import evaluate_harness_result
from selfforge_agent.verification.junit import MAX_JUNIT_BYTES
from selfforge_agent.verification.runner import TrustRequiredError


def _read_junit_file(xml_path: Path) -> bytes | None:
    """Read JUnit XML using safe non-blocking and nofollow file descriptor."""
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


def _resolve_pytest_selection(
    repo: Path, cwd_str: str, raw_selection: PytestSelection | dict | None
) -> tuple[PytestSelection, Path, Path]:
    """Resolve and validate selection against repository and cwd."""
    if raw_selection is None:
        selection = PytestSelection()
    elif isinstance(raw_selection, PytestSelection):
        selection = raw_selection
    elif isinstance(raw_selection, dict):
        selection = PytestSelection(**raw_selection)
    else:
        raise ValueError("Invalid selection specification for pytest")

    cwd_path = (repo / cwd_str).resolve()
    if not cwd_path.is_relative_to(repo) or not cwd_path.is_dir():
        raise ValueError("cwd must be an existing directory inside repository")

    try:
        target = (cwd_path / selection.test_path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Failed to resolve test_path: {exc}") from exc

    if not target.is_relative_to(repo) or not target.exists():
        raise ValueError("test_path must exist inside the repository")
    if any(is_protected_name(part) for part in target.relative_to(repo).parts):
        raise ValueError("test_path resolves to a protected path")
    if not (target.is_file() or target.is_dir()):
        raise ValueError("test_path must be a regular file or directory")

    return selection, cwd_path, target


class PytestAdapter(BaseHarnessAdapter):
    """Pytest framework adapter implementing the shared test harness protocol."""

    adapter_id = "pytest"
    adapter_version = "1.0"

    def validate_request(self, request: TestExecutionRequest) -> None:
        """Validate request preconditions without starting any child processes."""
        if request.sandbox == "host" and not request.trusted:
            raise TrustRequiredError("Pass trusted=True only for a repository you trust")
        repo = request.repository.resolve()
        _resolve_pytest_selection(repo, request.cwd, request.selection)

    def create_plan(self, request: TestExecutionRequest) -> TestPlan:
        """Create a deterministic, frozen test execution plan."""
        self.validate_request(request)
        repo = request.repository.resolve()
        selection, _cwd_path, target = _resolve_pytest_selection(repo, request.cwd, request.selection)

        selection_summary = selection.test_path
        if selection.keyword:
            selection_summary += f" (keyword={selection.keyword!r})"

        if request.sandbox == "host":
            env_desc = f"local; python={sys.version.split()[0]}; platform={platform.platform()}"
            cmd = ["python", "-m", "pytest", str(target)]
            if selection.keyword is not None:
                cmd.extend(["-k", selection.keyword])
            cmd.append("--junitxml={junit_xml}")
        else:
            env_desc = f"docker; image={request.image}; network=none"
            rel_target = target.relative_to(repo).as_posix()
            cmd = ["python", "-m", "pytest", rel_target]
            if selection.keyword is not None:
                cmd.extend(["-k", selection.keyword])
            cmd.append("--junitxml=/artifacts/junit.xml")

        plan_data = {
            "schema_version": "1.0",
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "backend": "pytest",
            "repository": str(repo),
            "cwd": request.cwd,
            "command": cmd,
            "environment": env_desc,
            "report_artifacts": ["junit.xml"],
            "selection_summary": selection_summary,
            "timeout": request.timeout,
            "sandbox": request.sandbox,
            "trusted": request.trusted,
            "image": request.image,
        }
        digest = compute_plan_digest(plan_data)

        return TestPlan(
            schema_version="1.0",
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            backend="pytest",
            repository=str(repo),
            cwd=request.cwd,
            command=cmd,
            environment=env_desc,
            report_artifacts=["junit.xml"],
            selection_summary=selection_summary,
            timeout=request.timeout,
            sandbox=request.sandbox,
            trusted=request.trusted,
            image=request.image,
            plan_digest=digest,
        )

    def execute_plan(self, plan: TestPlan) -> TestRunResult:
        """Execute the frozen plan and return a normalized TestRunResult."""
        repo = Path(plan.repository).resolve()

        if plan.sandbox == "host":
            if not plan.trusted:
                raise TrustRequiredError("Pass trusted=True only for a repository you trust")

            with tempfile.TemporaryDirectory(prefix="selfforge-agent-") as tempdir:
                xml_path = Path(tempdir) / "junit.xml"
                pycache_dir = Path(tempdir) / "pycache"

                cmd = [
                    sys.executable,
                    "-B",
                    "-X",
                    f"pycache_prefix={pycache_dir}",
                    "-m",
                    "pytest",
                ]
                # Reconstruct command arguments from plan.command
                # Skip the dummy python/pytest prefix and format the junitxml
                for arg in plan.command[3:]:
                    if arg == "--junitxml={junit_xml}":
                        cmd.append(f"--junitxml={xml_path}")
                    else:
                        cmd.append(arg)

                start = time.monotonic()
                timed_out = False
                try:
                    process = subprocess.Popen(
                        cmd,
                        cwd=repo / plan.cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=(os.name == "posix"),
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    duration = time.monotonic() - start
                    return evaluate_harness_result(
                        plan=plan,
                        exit_code=1,
                        timed_out=False,
                        cancelled=False,
                        duration_seconds=duration,
                        stdout="",
                        stderr=str(exc),
                        xml_bytes=None,
                    )

                try:
                    stdout, stderr = process.communicate(timeout=plan.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    timed_out = True
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    try:
                        stdout, stderr = process.communicate()
                    except (subprocess.TimeoutExpired, OSError, ValueError):
                        stdout, stderr = b"", b""
                    if isinstance(exc, KeyboardInterrupt):
                        raise

                duration = time.monotonic() - start
                xml_bytes = _read_junit_file(xml_path)

                return evaluate_harness_result(
                    plan=plan,
                    exit_code=None if timed_out else process.returncode,
                    timed_out=timed_out,
                    cancelled=False,
                    duration_seconds=duration,
                    stdout=stdout.decode("utf-8", "replace")[-20000:],
                    stderr=stderr.decode("utf-8", "replace")[-20000:],
                    xml_bytes=xml_bytes,
                )

        else:
            # Docker execution path
            executor = DockerExecutor(repo, image=plan.image)
            cmd_str = shlex.join(plan.command)
            start = time.monotonic()
            outcome, xml_bytes = executor.run_with_artifact(cmd_str, plan.timeout, "junit.xml")
            duration = time.monotonic() - start

            return evaluate_harness_result(
                plan=plan,
                exit_code=outcome.returncode,
                timed_out=outcome.timed_out,
                cancelled=False,
                duration_seconds=duration,
                stdout=outcome.output[-20000:],
                stderr="",
                xml_bytes=xml_bytes,
            )
