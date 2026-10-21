"""Offline end-to-end repair demo with a fixed proposer for the known fixture only."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from selfforge_agent.coding.agent import RepairAgent
from selfforge_agent.coding.models import CodeEdit, RepairObservation
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner


class KnownFixtureModel:
    def propose(self, _observation: RepairObservation) -> CodeEdit:
        return CodeEdit(
            path="calculator.py",
            old_text="return a - b",
            new_text="return a + b",
            rationale="Restore the original addition behavior",
        )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    parser.add_argument("--trust-local-tests", action="store_true")
    args = parser.parse_args()
    if args.sandbox == "host" and not args.trust_local_tests:
        parser.error("Host tests require --trust-local-tests")
    with tempfile.TemporaryDirectory(prefix="verified-pr-demo-") as temporary:
        repo = Path(temporary) / "repo"
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("create_demo.py")), str(repo)],
            check=True,
        )
        base = git(repo, "rev-list", "--max-parents=0", "HEAD")
        head = git(repo, "rev-parse", "HEAD")
        runner = TestRunner() if args.sandbox == "host" else DockerTestRunner()
        report = RepairAgent(KnownFixtureModel(), runner=runner).run(
            repo, base, head, "Fix the add regression", trusted=args.trust_local_tests
        )
        print(f"Status: {report.status}")
        print(f"Base: {report.base_test.status}; Head: {report.head_test.status}")
        for attempt in report.attempts:
            print(f"Attempt {attempt.number}: {attempt.test_result.status}")
        print(report.verified_patch)
        if report.status != "verified":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
