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
