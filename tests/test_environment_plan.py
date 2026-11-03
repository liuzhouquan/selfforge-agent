"""Tests for environment preparation plan generation and wheelhouse auditing."""

import hashlib
import json
from pathlib import Path

import pytest

from selfforge_agent.runtime.environment import plan_environment_preparation

SAMPLE_UV_LOCK = """
version = 1
revision = 1
requires-python = ">=3.11"

[[package]]
name = "flask"
version = "3.0.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://example.com/flask-3.0.0-py3-none-any.whl", hash = "sha256:1111111111111111111111111111111111111111111111111111111111111111" }
]

[[package]]
name = "click"
version = "8.1.7"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://example.com/click-8.1.7-py3-none-any.whl", hash = "sha256:2222222222222222222222222222222222222222222222222222222222222222" }
]
"""


def test_plan_environment_with_missing_wheels(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(SAMPLE_UV_LOCK, encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    plan = plan_environment_preparation(repo, wheelhouse=wheelhouse, tag="test-app:locked")

    assert plan["version"] == 1
    assert plan["status"] == "pending_wheels"
    assert plan["ready_to_build"] is False
    assert plan["tag"] == "test-app:locked"
    assert plan["package_count"] == 2
    assert plan["wheelhouse"]["total_wheels"] == 0
    assert plan["wheelhouse"]["missing_count"] == 2
    assert "pip download" in plan["download_command"]
    assert "--require-hashes" in plan["download_command"]
    assert "selfforge-agent sandbox build" in plan["build_command"]


def test_plan_environment_ready_when_wheels_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(SAMPLE_UV_LOCK, encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    # Create matching wheels with the exact expected sha256
    # For flask: sha256:1111... -> we can mock or write content and put matching hash
    content_flask = b"dummy flask content"
    hash_flask = hashlib.sha256(content_flask).hexdigest()
    content_click = b"dummy click content"
    hash_click = hashlib.sha256(content_click).hexdigest()

    custom_lock = f"""
version = 1
revision = 1
requires-python = ">=3.11"

[[package]]
name = "flask"
version = "3.0.0"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
    {{ url = "https://example.com/flask.whl", hash = "sha256:{hash_flask}" }}
]

[[package]]
name = "click"
version = "8.1.7"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
    {{ url = "https://example.com/click.whl", hash = "sha256:{hash_click}" }}
]
"""
    (repo / "uv.lock").write_text(custom_lock, encoding="utf-8")
    (wheelhouse / "flask-3.0.0-py3-none-any.whl").write_bytes(content_flask)
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(content_click)

    plan = plan_environment_preparation(repo, wheelhouse=wheelhouse, tag="test-app:locked")

    assert plan["status"] == "ready"
    assert plan["ready_to_build"] is True
    assert plan["wheelhouse"]["missing_count"] == 0
    assert plan["wheelhouse"]["satisfied_count"] == 2
    assert plan["wheelhouse"]["total_wheels"] == 2


def test_plan_environment_rejects_missing_lock(tmp_path: Path) -> None:
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="No supported lockfile"):
        plan_environment_preparation(repo)


def test_plan_environment_cli_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from selfforge_agent.cli import main as cli_main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(SAMPLE_UV_LOCK, encoding="utf-8")

    code = cli_main(["sandbox", "plan", "--repo", str(repo), "--tag", "cli-test:locked"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["tag"] == "cli-test:locked"
    assert data["status"] == "pending_wheels"


def test_plan_environment_api_endpoint(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app

    client = TestClient(app)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(SAMPLE_UV_LOCK, encoding="utf-8")

    res = client.post("/api/sandbox/plan", json={"repository": str(repo), "tag": "api-plan:1.0"})
    assert res.status_code == 200
    plan = res.json()
    assert plan["tag"] == "api-plan:1.0"
    assert plan["status"] == "pending_wheels"
    assert plan["package_count"] == 2
