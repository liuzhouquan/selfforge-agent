"""Tests for repository environment and dependency detection."""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.cli import main
from selfforge_agent.runtime.environment import detect_repository_environment


def _init_git(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main", "-q"], check=True)
    (path / "README.md").write_text("# Project\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(path),
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", "Initial",
        ],
        check=True,
    )
    return path


def test_detect_flat_layout_with_requirements(tmp_path: Path) -> None:
    repo = _init_git(tmp_path / "flat_repo")
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("requests==2.31.0\npytest>=8.0\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    env = detect_repository_environment(repo)
    assert env["layout"] == "flat"
    assert "requirements.txt" in env["dependency_files"]
    assert "tests" in env["test_paths"]
    assert env["test_framework"] == "pytest"
    assert env["has_hash_lock"] is False
    assert any("hash" in w.lower() for w in env["warnings"])


def test_detect_src_layout_with_pyproject_and_uv_lock(tmp_path: Path) -> None:
    repo = _init_git(tmp_path / "src_repo")
    src_dir = repo / "src" / "pkg"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("VERSION = '0.1.0'\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        """
[project]
name = "pkg"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi", "pydantic>=2.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text(
        """
version = 1
[[package]]
name = "pkg"
version = "0.1.0"
source = { virtual = "." }
""",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pkg.py").write_text("def test_pkg(): pass\n", encoding="utf-8")

    env = detect_repository_environment(repo)
    assert env["layout"] == "src"
    assert "pyproject.toml" in env["dependency_files"]
    assert "uv.lock" in env["lock_files"]
    assert env["python_requires"] == ">=3.11"
    assert "tests" in env["test_paths"]
    assert env["test_framework"] == "pytest"


def test_detect_hash_locked_requirements(tmp_path: Path) -> None:
    repo = _init_git(tmp_path / "locked_repo")
    (repo / "requirements.lock").write_text(
        "itsdangerous==2.2.0 --hash=sha256:e005490a6034d33b5a1538356a87754d9c79f42b3ac2b737f5d0f622d16ebbb4\n",
        encoding="utf-8",
    )

    env = detect_repository_environment(repo)
    assert "requirements.lock" in env["lock_files"]
    assert env["has_hash_lock"] is True


def test_web_environment_endpoint(tmp_path: Path) -> None:
    repo = _init_git(tmp_path / "web_repo")
    (repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    client = TestClient(api_module.app)
    resp = client.get("/api/code/environment", params={"repository": str(repo)})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["repository"] == str(repo.resolve())
    assert "pyproject.toml" in data["dependency_files"]

    # Non-existent repository
    bad_resp = client.get("/api/code/environment", params={"repository": str(tmp_path / "nonexistent")})
    assert bad_resp.status_code == 400


def test_cli_sandbox_detect(monkeypatch, capsys, tmp_path: Path) -> None:
    repo = _init_git(tmp_path / "cli_repo")
    (repo / "pyproject.toml").write_text("[project]\nname='cli-test'\n", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["selfforge-agent", "sandbox", "detect", "--repo", str(repo)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "pyproject.toml" in captured.out
    assert "layout" in captured.out.lower()
