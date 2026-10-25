"""Offline smoke test for repeated, hidden-test Coding Agent evaluation."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from selfforge_agent.providers import route_providers
from selfforge_agent.runtime.benchmark import evaluate_suite
from selfforge_agent.runtime.evaluation import CASES, OfflineFixtureModel
from selfforge_agent.runtime.model import OpenAIChatAdapter


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Use the configured DeepSeek model and Docker on one synthetic task")
    args = parser.parse_args()
    case = CASES[0]
    with tempfile.TemporaryDirectory(prefix="selfforge-benchmark-demo-") as temp:
        root = Path(temp)
        repository = root / "project"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.name", "Fixture")
        git(repository, "config", "user.email", "fixture@example.invalid")
        (repository / "app.py").write_text(case.source, encoding="utf-8")
        (repository / "tests").mkdir()
        (repository / "tests" / "test_app.py").write_text(case.test, encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-qm", "Broken baseline")
        (root / "test_holdout.py").write_text(
            "from app import add\n\ndef test_holdout():\n    assert add(-4, 9) == 5\n",
            encoding="utf-8",
        )
        manifest = root / "suite.json"
        manifest.write_text(json.dumps({"version": 1, "cases": [{
            "id": "addition", "repository": "project", "revision": git(repository, "rev-parse", "HEAD"),
            "task": case.task, "allowed_changes": ["app.py"],
            "holdout_test": "test_holdout.py",
        }]}), encoding="utf-8")
        if args.live:
            providers = route_providers("deepseek", False)
            factory = lambda _case: OpenAIChatAdapter(providers)
        else:
            factory = lambda _case: OfflineFixtureModel(case)
        report = evaluate_suite(manifest, factory, repeats=1 if args.live else 2,
                                sandbox="docker" if args.live else "host",
                                trusted_host=not args.live)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
