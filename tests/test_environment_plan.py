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


@pytest.mark.parametrize("text", ["--extra-index-url https://example.invalid\n", "flask>=3\n", "flask==3.0.0\n", "flask==3.0.0 --hash=md5:abcd\n"])
def test_plan_rejects_unsupported_lock(tmp_path, text):
    (tmp_path / "requirements.lock").write_text(text)
    with pytest.raises(ValueError):
        plan_environment_preparation(tmp_path)


def test_plan_continuations_and_exported_commands(tmp_path):
    import shlex
    wheelhouse = tmp_path / "wheels;echo unsafe"
    wheelhouse.mkdir()
    wheel = wheelhouse / "demo-1-py3-none-any.whl"
    wheel.write_bytes(b"demo")
    digest = hashlib.sha256(b"demo").hexdigest()
    lock = tmp_path / "requirements.lock"
    lock.write_text("demo==1 \\\n    --hash=sha256:" + digest + "\n")
    plan = plan_environment_preparation(tmp_path, wheelhouse=wheelhouse)
    assert plan["ready_to_build"]
    assert shlex.split(plan["download_command"])[-3:] == [str(wheelhouse), "-r", str(lock)]
    lock.unlink()
    wheel.unlink()
    (tmp_path / "uv.lock").write_text(SAMPLE_UV_LOCK)
    plan = plan_environment_preparation(tmp_path, wheelhouse=wheelhouse)
    assert "uv.lock" not in shlex.split(plan["download_command"])[-1]
    assert plan["export_command"]


def test_plan_rejects_extra_or_symlink_wheel(tmp_path):
    (tmp_path / "requirements.lock").write_text("demo==1 --hash=sha256:" + "0" * 64)
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    wheel = wheelhouse / "demo-1-py3-none-any.whl"
    wheel.write_bytes(b"unknown")
    with pytest.raises(ValueError, match="absent from lock"):
        plan_environment_preparation(tmp_path, wheelhouse=wheelhouse)
    wheel.unlink()
    wheel.symlink_to(tmp_path / "requirements.lock")
    with pytest.raises(ValueError, match="symlink"):
        plan_environment_preparation(tmp_path, wheelhouse=wheelhouse)


def test_plan_rejects_unsafe_tag(tmp_path):
    (tmp_path / "uv.lock").write_text(SAMPLE_UV_LOCK)
    with pytest.raises(ValueError, match="image name"):
        plan_environment_preparation(tmp_path, tag="tag; touch injected")


@pytest.mark.parametrize("kind", ["lock", "wheelhouse"])
def test_plan_api_preserves_symlink_checks(tmp_path, kind):
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app
    repo = tmp_path / "repo"
    repo.mkdir()
    lock = repo / "requirements.lock"
    lock.write_text("demo==1 --hash=sha256:" + "0" * 64)
    payload = {"repository": str(repo)}
    if kind == "lock":
        link = tmp_path / "symlink.lock"
        link.symlink_to(lock)
        payload["requirements_file"] = str(link)
    else:
        target = tmp_path / "wheels"
        target.mkdir()
        link = tmp_path / "link-wheels"
        link.symlink_to(target)
        payload["wheelhouse"] = str(link)
    response = TestClient(app).post("/api/sandbox/plan", json=payload)
    assert response.status_code == 400
    assert "symlink" in response.json()["detail"]


def test_plan_reuses_bounded_dependency_inputs(tmp_path, monkeypatch):
    from selfforge_agent.runtime import images
    lock = tmp_path / "requirements.lock"
    data = b"wheel"
    lock.write_text("demo==1 --hash=sha256:" + hashlib.sha256(data).hexdigest())
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "demo-1-py3-none-any.whl").write_bytes(data)
    monkeypatch.setattr(images, "MAX_WHEEL_BYTES", 4)
    with pytest.raises(ValueError, match="size limit"):
        plan_environment_preparation(tmp_path, wheelhouse=wheels)
    lock.write_bytes(b"x" * (images.MAX_LOCK_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        plan_environment_preparation(tmp_path)


@pytest.mark.parametrize("filename", ["requirements.toml", "uv.lock"])
def test_explicit_requirements_format_does_not_depend_on_filename(tmp_path, filename):
    import shlex
    requirements = tmp_path / filename
    requirements.write_text("demo==1 --hash=sha256:" + "0" * 64)
    plan = plan_environment_preparation(tmp_path, requirements_file=requirements)
    assert plan["export_command"] is None
    assert shlex.split(plan["download_command"])[-1] == str(requirements)


def test_plan_environment_with_poetry_lock(tmp_path: Path) -> None:
    sample_poetry_lock = """
[[package]]
name = "flask"
version = "3.0.0"
files = [
    {file = "flask-3.0.0-py3-none-any.whl", hash = "sha256:1111111111111111111111111111111111111111111111111111111111111111"},
]

[[package]]
name = "click"
version = "8.1.7"
files = [
    {file = "click-8.1.7-py3-none-any.whl", hash = "sha256:2222222222222222222222222222222222222222222222222222222222222222"},
]
"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "poetry.lock").write_text(sample_poetry_lock, encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    plan = plan_environment_preparation(repo, wheelhouse=wheelhouse, tag="poetry-app:locked")
    assert plan["version"] == 1
    assert plan["status"] == "pending_wheels"
    assert plan["ready_to_build"] is False
    assert plan["package_count"] == 2
    assert "poetry.lock" in plan["export_command"]


def test_plan_environment_with_explicit_poetry_lock(tmp_path: Path) -> None:
    sample_poetry_lock = """
[[package]]
name = "six"
version = "1.16.0"
files = [
    {file = "six-1.16.0-py2.py3-none-any.whl", hash = "sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"},
]
"""
    repo = tmp_path / "repo"
    repo.mkdir()
    lock = repo / "custom_poetry.lock"
    lock.write_text(sample_poetry_lock, encoding="utf-8")

    plan = plan_environment_preparation(repo, lock_file=lock)
    assert plan["version"] == 1
    assert plan["package_count"] == 1
    assert "custom_poetry.lock" in plan["export_command"]


def test_cli_sandbox_export_lock_poetry(monkeypatch, capsys, tmp_path: Path) -> None:
    from selfforge_agent.cli import main

    sample_poetry_lock = """
[[package]]
name = "six"
version = "1.16.0"
files = [
    {file = "six-1.16.0-py2.py3-none-any.whl", hash = "sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"},
]
"""
    lock = tmp_path / "poetry.lock"
    lock.write_text(sample_poetry_lock, encoding="utf-8")
    out_file = tmp_path / "requirements.txt"

    monkeypatch.setattr(
        "sys.argv",
        ["selfforge-agent", "sandbox", "export-lock", "--lock", str(lock), "--output", str(out_file)],
    )
    assert main() == 0
    captured = capsys.readouterr()
    assert f"Exported hash-locked requirements to {out_file}" in captured.out
    assert out_file.is_file()
    assert "six==1.16.0 --hash=sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2" in out_file.read_text(encoding="utf-8")
