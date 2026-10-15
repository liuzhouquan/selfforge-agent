"""Create a reproducible, known local repository with a failing head commit."""

import argparse
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    repo = parser.parse_args().destination.resolve()
    repo.mkdir(parents=True, exist_ok=False)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Demo")
    git(repo, "config", "user.email", "demo@example.invalid")
    (repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_calculator.py").write_text(
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
    print(f"repo={repo}\nbase={base}\nhead={git(repo, 'rev-parse', 'HEAD')}")


if __name__ == "__main__":
    main()
