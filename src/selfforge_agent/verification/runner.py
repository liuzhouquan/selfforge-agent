"""Explicitly trusted local pytest execution; a sandbox can replace this backend later."""

import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from selfforge_agent.models import TestResult


class TrustRequiredError(ValueError):
    """Running repository code requires an explicit trust choice."""


class TestRunner:
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
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        repo = repository.resolve()
        target = (repo / test_path).resolve()
        if not target.is_relative_to(repo) or not target.exists():
            raise ValueError("test_path must exist inside the repository")
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
            counts = {key: 0 for key in ("passed", "failed", "errors", "skipped")}
            parse_error = None
            if xml_path.exists():
                try:
                    root = ET.parse(xml_path).getroot()
                    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
                    total = sum(int(s.get("tests", "0")) for s in suites)
                    counts["failed"] = sum(int(s.get("failures", "0")) for s in suites)
                    counts["errors"] = sum(int(s.get("errors", "0")) for s in suites)
                    counts["skipped"] = sum(int(s.get("skipped", "0")) for s in suites)
                    counts["passed"] = max(
                        0, total - counts["failed"] - counts["errors"] - counts["skipped"]
                    )
                except (ET.ParseError, ValueError) as exc:
                    parse_error = f"Invalid JUnit XML: {exc}"
            elif not timed_out:
                parse_error = "pytest did not produce JUnit XML"
            if timed_out:
                status, error = "timeout", f"pytest exceeded {timeout:g} seconds"
            elif parse_error:
                status, error = "error", parse_error
            elif process.returncode == 0:
                status, error = "passed", None
            elif counts["failed"] or counts["errors"]:
                status, error = "failed", None
            else:
                status, error = "error", f"pytest exited with code {process.returncode}"
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
