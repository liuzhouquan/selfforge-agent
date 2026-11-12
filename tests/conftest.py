import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def configured_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic rate cards test arithmetic; these are not published provider prices."""
    card = {"prompt_per_million": 0.14, "completion_per_million": 0.28,
            "source": "synthetic-test-card", "as_of": "2026-10-31"}
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", json.dumps({
        "deepseek": {"deepseek-chat": card},
        "fixture": {"deepseek-chat": card},
        "openrouter": {"other-model": {**card, "prompt_per_million": 1.0,
                                        "completion_per_million": 2.0}},
    }))


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def demo_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "demo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base works")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "calculator.py").write_text(
        "def add(a, b):\n    eval('1 + 1')\n    return a - b\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "introduce regression")
    return repo, base, git(repo, "rev-parse", "HEAD")


@pytest.fixture
def structure_manifest(tmp_path):
    """Exercise the real manifest loader against a hermetic Git fixture, no upstream execution."""
    def prepare(manifest):
        data = json.loads(manifest.read_text())
        repo = tmp_path / "structure-repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "structure fixture")
        for case in data["cases"]:
            case["repository"] = str(repo)
            case["revision"] = git(repo, "rev-parse", "HEAD")
            for key in ("holdout_test", "reference_patch"):
                case[key] = str((manifest.parent / case[key]).resolve())
        path = tmp_path / "structure.json"
        path.write_text(json.dumps(data))
        return path
    return prepare


@pytest.fixture
def qualification_manifest(tmp_path):
    """Explicitly provisioned sources; upstream code executes only in Docker.

    CI fetches pinned source before the integration group. Image overrides create
    a distinct temporary manifest/report hash, never mutate the frozen candidate.
    """
    import os
    root = Path(os.environ.get("SELFFORGE_EVAL_SOURCE_ROOT",
                              Path(__file__).resolve().parents[1] / ".local/benchmark/repos"))

    def prepare(manifest):
        data = json.loads(manifest.read_text())
        for case in data["cases"]:
            source = root / Path(case["repository"]).name
            if not source.is_dir():
                pytest.fail("Provision pinned candidate sources before integration tests; see docs/benchmark.md")
            case["repository"] = str(source.resolve())
            for key in ("holdout_test", "reference_patch"):
                case[key] = str((manifest.parent / case[key]).resolve())
            if os.environ.get("SELFFORGE_EVAL_IMAGE"):
                case["image"] = os.environ["SELFFORGE_EVAL_IMAGE"]
        path = tmp_path / manifest.name
        path.write_text(json.dumps(data))
        return path
    return prepare
