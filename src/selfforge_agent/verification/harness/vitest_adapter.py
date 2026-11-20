"""Vitest framework adapter supporting Host and isolated Docker execution."""

import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from selfforge_agent.runtime.execution import DockerExecutor
from selfforge_agent.verification.harness.base_adapter import BaseHarnessAdapter
from selfforge_agent.verification.harness.contracts import (
    JS_TS_EXTENSIONS,
    HarnessReasonCode,
    HarnessStatus,
    TestExecutionRequest,
    TestPlan,
    TestRunResult,
    VitestSelection,
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


def _resolve_vitest_selection(
    repo: Path, cwd_str: str, raw_selection: VitestSelection | dict | None
) -> tuple[VitestSelection, Path, Path | None, Path | None]:
    """Resolve and validate selection against repository and cwd."""
    if raw_selection is None:
        selection = VitestSelection()
    elif isinstance(raw_selection, VitestSelection):
        selection = raw_selection
    elif isinstance(raw_selection, dict):
        selection = VitestSelection(**raw_selection)
    else:
        raise ValueError("Invalid selection specification for vitest")

    cwd_path = (repo / cwd_str).resolve()
    if not cwd_path.is_relative_to(repo) or not cwd_path.is_dir():
        raise ValueError("cwd must be an existing directory inside repository")

    target_path: Path | None = None
    if selection.test_path is not None:
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
        if target.is_file():
            ext = target.suffix.lower()
            if ext not in JS_TS_EXTENSIONS:
                raise ValueError(
                    f"test_path {selection.test_path!r} is not a JavaScript/TypeScript test file "
                    f"(supported extensions: {sorted(JS_TS_EXTENSIONS)})"
                )
        target_path = target

    config_path: Path | None = None
    if selection.config_file is not None:
        if "jest" in selection.config_file.lower():
            raise ValueError("Jest configuration is not supported by Vitest adapter")
        try:
            cfg = (cwd_path / selection.config_file).resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Failed to resolve config_file: {exc}") from exc

        if not cfg.is_relative_to(repo) or not cfg.is_file():
            raise ValueError("config_file must exist as a regular file inside the repository")
        if any(is_protected_name(part) for part in cfg.relative_to(repo).parts):
            raise ValueError("config_file resolves to a protected path")
        if "jest" in cfg.name.lower():
            raise ValueError("Jest configuration is not supported by Vitest adapter")
        config_path = cfg

    return selection, cwd_path, target_path, config_path


class VitestAdapter(BaseHarnessAdapter):
    """Vitest framework adapter implementing the shared test harness protocol."""

    adapter_id = "vitest"
    adapter_version = "1.0"

    def validate_request(self, request: TestExecutionRequest) -> None:
        """Validate request preconditions without starting child processes."""
        if request.sandbox == "host" and not request.trusted:
            raise TrustRequiredError("Pass trusted=True only for a repository you trust")
        repo = request.repository.resolve()
        _resolve_vitest_selection(repo, request.cwd, request.selection)

    def create_plan(self, request: TestExecutionRequest) -> TestPlan:
        """Create a deterministic, frozen test execution plan."""
        self.validate_request(request)
        repo = request.repository.resolve()
        selection, _cwd_path, target, config_path = _resolve_vitest_selection(
            repo, request.cwd, request.selection
        )

        parts = []
        if selection.test_path:
            parts.append(selection.test_path)
        if selection.test_name_pattern:
            parts.append(f"pattern={selection.test_name_pattern!r}")
        selection_summary = " ".join(parts) if parts else "all tests"

        if request.sandbox == "host":
            node_path = shutil.which("node")
            env_desc = f"local; node={'available' if node_path else 'unavailable'}; adapter=vitest"
            cmd = ["vitest", "run"]
            if target is not None:
                cmd.append(str(target))
            if selection.test_name_pattern is not None:
                cmd.extend(["-t", selection.test_name_pattern])
            if config_path is not None:
                cmd.extend(["--config", str(config_path)])
            cmd.extend(["--reporter=junit", "--outputFile={junit_xml}"])
        else:
            env_desc = f"docker; image={request.image}; network=none"
            cmd = ["vitest", "run"]
            if target is not None:
                rel_target = target.relative_to(repo).as_posix()
                cmd.append(rel_target)
            if selection.test_name_pattern is not None:
                cmd.extend(["-t", selection.test_name_pattern])
            if config_path is not None:
                rel_config = config_path.relative_to(repo).as_posix()
                cmd.extend(["--config", rel_config])
            cmd.extend(["--reporter=junit", "--outputFile=/artifacts/junit.xml"])

        plan_data = {
            "schema_version": "1.0",
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "backend": "vitest",
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
            backend="vitest",
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

            cwd_path = (repo / plan.cwd).resolve()
            local_vitest = cwd_path / "node_modules" / ".bin" / "vitest"
            repo_vitest = repo / "node_modules" / ".bin" / "vitest"
            system_vitest = shutil.which("vitest")
            node_binary = shutil.which("node")

            if not node_binary:
                return TestRunResult(
                    run_id=f"run-{uuid.uuid4().hex[:16]}",
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    plan_digest=plan.plan_digest,
                    status=HarnessStatus.ERROR,
                    reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                    exit_code=None,
                    duration_seconds=0.0,
                    error_message="Environment unavailable: Node.js runtime is not installed on host",
                    environment=plan.environment,
                    command=plan.command,
                )

            if local_vitest.is_file():
                exec_cmd = [str(local_vitest)]
            elif repo_vitest.is_file():
                exec_cmd = [str(repo_vitest)]
            elif system_vitest:
                exec_cmd = [system_vitest]
            else:
                return TestRunResult(
                    run_id=f"run-{uuid.uuid4().hex[:16]}",
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    plan_digest=plan.plan_digest,
                    status=HarnessStatus.ERROR,
                    reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                    exit_code=None,
                    duration_seconds=0.0,
                    error_message="Environment unavailable: Vitest executable is not installed on host",
                    environment=plan.environment,
                    command=plan.command,
                )

            with tempfile.TemporaryDirectory(prefix="selfforge-vitest-") as tempdir:
                xml_path = Path(tempdir) / "junit.xml"

                cmd = list(exec_cmd)
                for arg in plan.command[1:]:
                    if arg == "--outputFile={junit_xml}":
                        cmd.append(f"--outputFile={xml_path}")
                    else:
                        cmd.append(arg)

                start = time.monotonic()
                timed_out = False
                try:
                    process = subprocess.Popen(
                        cmd,
                        cwd=cwd_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=(os.name == "posix"),
                    )
                except FileNotFoundError:
                    return TestRunResult(
                        run_id=f"run-{uuid.uuid4().hex[:16]}",
                        adapter_id=self.adapter_id,
                        adapter_version=self.adapter_version,
                        plan_digest=plan.plan_digest,
                        status=HarnessStatus.ERROR,
                        reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                        exit_code=None,
                        duration_seconds=0.0,
                        error_message="Environment unavailable: vitest binary not found",
                        environment=plan.environment,
                        command=cmd,
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

            # Environment unavailable check inside container
            if outcome.returncode == 127 and any(
                phrase in outcome.output
                for phrase in ("vitest: not found", "node: not found", "command not found: vitest")
            ):
                return TestRunResult(
                    run_id=f"run-{uuid.uuid4().hex[:16]}",
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    plan_digest=plan.plan_digest,
                    status=HarnessStatus.ERROR,
                    reason_code=HarnessReasonCode.ENVIRONMENT_UNAVAILABLE,
                    exit_code=127,
                    duration_seconds=duration,
                    stdout=outcome.output[-20000:],
                    stderr="",
                    error_message="Environment unavailable: Node.js or Vitest toolchain missing in container",
                    environment=plan.environment,
                    command=plan.command,
                )

            cancelled = "Workspace staging cancelled" in outcome.output
            timed_out = outcome.timed_out

            return evaluate_harness_result(
                plan=plan,
                exit_code=outcome.returncode,
                timed_out=timed_out,
                cancelled=cancelled,
                duration_seconds=duration,
                stdout=outcome.output[-20000:],
                stderr="",
                xml_bytes=xml_bytes,
            )
