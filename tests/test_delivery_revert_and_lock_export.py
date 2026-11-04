"""Tests for workspace revert workflow, lock export, and repair environment configuration."""

from pathlib import Path

import pytest

from selfforge_agent.api.app import GitHubRepairRequest
from selfforge_agent.runtime.delivery import revert_workspace
from selfforge_agent.runtime.environment import export_hash_locked_requirements
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def test_revert_workspace_restores_tracked_and_deletes_untracked(demo_repo: tuple[Path, str, str]) -> None:
    repo, _base, _head = demo_repo
    manager = CodingWorkspaceManager()
    session_id = "reverttest01"
    workspace = manager.create(repo, session_id)
    session_root = manager.session_root(repo, session_id)
    session = SessionStore(session_root, session_id)
    session.record_verification(True, workspace_sha256="0" * 64)
    assert session.verification_passed() is True

    # Modify an existing tracked file
    calc = workspace.path / "calculator.py"
    calc.write_text("corrupted content", encoding="utf-8")

    # Create a new untracked file
    extra = workspace.path / "untracked.py"
    extra.write_text("print('hello')", encoding="utf-8")

    # Create a protected file that must NOT be deleted by revert
    env_file = workspace.path / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")

    # Run revert
    result = revert_workspace(workspace, session)
    assert result.session_id == session_id
    assert "calculator.py" in result.reverted_files
    assert "untracked.py" in result.reverted_files

    # Verify tracked file was restored
    assert "def add" in calc.read_text(encoding="utf-8")

    # Verify untracked file was deleted
    assert not extra.exists()

    # Verify protected file was untouched
    assert env_file.exists()

    # Verify verification evidence was invalidated
    assert session.verification_passed() is False

    # Second revert on clean workspace returns empty list
    second = revert_workspace(workspace, session)
    assert second.reverted_files == []

    env_file.unlink()
    manager.remove(repo, session_id)


def test_export_hash_locked_requirements_from_uv_lock(tmp_path: Path) -> None:
    sample_lock = """
version = 1
revision = 1
requires-python = ">=3.11"

[[package]]
name = "flask"
version = "3.0.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://example.com/flask.whl", hash = "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789" },
    { url = "https://example.com/flask-other.whl", hash = "sha256:1111111111111111111111111111111111111111111111111111111111111111" },
]

[[package]]
name = "click"
version = "8.1.7"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://example.com/click.whl", hash = "sha256:2222222222222222222222222222222222222222222222222222222222222222" },
]
"""
    lock_file = tmp_path / "uv.lock"
    lock_file.write_text(sample_lock, encoding="utf-8")

    output_file = tmp_path / "requirements.lock"
    exported = export_hash_locked_requirements(lock_file, output_file)

    lines = exported.strip().splitlines()
    assert len(lines) == 2
    assert "click==8.1.7" in lines[0]
    assert "sha256:2222222222222222222222222222222222222222222222222222222222222222" in lines[0]
    assert "flask==3.0.0" in lines[1]
    assert "sha256:1111111111111111111111111111111111111111111111111111111111111111" in lines[1]
    assert "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789" in lines[1]
    assert output_file.read_text(encoding="utf-8") == exported


def test_export_hash_locked_requirements_rejects_invalid_file(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.lock"
    with pytest.raises(ValueError, match="does not exist"):
        export_hash_locked_requirements(missing)

    invalid_toml = tmp_path / "invalid.lock"
    invalid_toml.write_text("not a toml [[[", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to parse"):
        export_hash_locked_requirements(invalid_toml)


@pytest.mark.parametrize("package", [
    'name = "unsafe\\n--index-url=bad"\nversion = "1.0"',
    'name = "missing-hash"\nversion = "1.0"',
    'name = "bad-wheel-list"\nversion = "1.0"\nwheels = 1',
])
def test_lock_export_rejects_incomplete_or_injectable_packages(tmp_path: Path, package: str) -> None:
    path = tmp_path / "uv.lock"
    path.write_text("[[package]]\n" + package + "\n")
    with pytest.raises(ValueError):
        export_hash_locked_requirements(path)


def test_lock_export_rejects_version_forks(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_text("\n".join(
        f'[[package]]\nname = "dependency"\nversion = "{version}"\n'
        f'wheels = [{{hash = "sha256:{"a" * 64}"}}]'
        for version in ("1.0", "2.0")
    ))
    with pytest.raises(ValueError, match="Version forks"):
        export_hash_locked_requirements(path)


def test_github_repair_request_supports_image_and_timeout() -> None:
    req = GitHubRepairRequest(
        url="https://github.com/org/repo/pull/1",
        task="Fix bug",
        image="custom-image:1.0",
        timeout=120.0,
    )
    assert req.image == "custom-image:1.0"
    assert req.timeout == 120.0


def test_export_hash_locked_requirements_from_poetry_lock(tmp_path: Path) -> None:
    sample_poetry_lock = """
[[package]]
name = "flask"
version = "3.0.0"
description = "A simple framework"
optional = false
python-versions = ">=3.8"
files = [
    {file = "flask-3.0.0-py3-none-any.whl", hash = "sha256:1111111111111111111111111111111111111111111111111111111111111111"},
    {file = "flask-3.0.0.tar.gz", hash = "sha256:2222222222222222222222222222222222222222222222222222222222222222"},
]

[[package]]
name = "click"
version = "8.1.7"
files = [
    {file = "click-8.1.7-py3-none-any.whl", hash = "sha256:3333333333333333333333333333333333333333333333333333333333333333"},
]
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(sample_poetry_lock, encoding="utf-8")
    exported = export_hash_locked_requirements(lock_file)
    lines = exported.strip().splitlines()
    assert len(lines) == 2
    assert lines[0] == "click==8.1.7 --hash=sha256:3333333333333333333333333333333333333333333333333333333333333333"
    assert "flask==3.0.0" in lines[1]
    assert "--hash=sha256:1111111111111111111111111111111111111111111111111111111111111111" in lines[1]
    assert "--hash=sha256:2222222222222222222222222222222222222222222222222222222222222222" in lines[1]


def test_export_hash_locked_requirements_poetry_v1_metadata(tmp_path: Path) -> None:
    poetry_v1_lock = """
[[package]]
name = "six"
version = "1.16.0"

[metadata.files]
six = [
    {file = "six-1.16.0-py2.py3-none-any.whl", hash = "sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"},
]
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(poetry_v1_lock, encoding="utf-8")
    exported = export_hash_locked_requirements(lock_file)
    assert exported.strip() == (
        "six==1.16.0 --hash=sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"
    )


def test_export_hash_locked_requirements_poetry_skips_editable_and_git(tmp_path: Path) -> None:
    lock_content = """
[[package]]
name = "my-internal-pkg"
version = "0.1.0"
source = { type = "git", url = "https://github.com/example/my-internal-pkg.git" }

[[package]]
name = "six"
version = "1.16.0"
files = [
    {file = "six-1.16.0-py2.py3-none-any.whl", hash = "8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"},
]
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(lock_content, encoding="utf-8")
    exported = export_hash_locked_requirements(lock_file)
    lines = exported.strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == "six==1.16.0 --hash=sha256:8abb2f1d86890a2dfb98308812e19c507c8d3302cc51453344d3d4173ad61dd2"


def test_export_hash_locked_requirements_poetry_missing_hashes_raises(tmp_path: Path) -> None:
    lock_content = """
[[package]]
name = "unhashed-pkg"
version = "1.0.0"
files = []
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(lock_content, encoding="utf-8")
    with pytest.raises(ValueError, match="Lock file includes an external package without supported SHA-256 hashes"):
        export_hash_locked_requirements(lock_file)


def test_export_hash_locked_requirements_poetry_version_conflict_raises(tmp_path: Path) -> None:
    lock_content = """
[[package]]
name = "pkg"
version = "1.0.0"
files = [
    {file = "pkg-1.0.0.whl", hash = "sha256:1111111111111111111111111111111111111111111111111111111111111111"},
]

[[package]]
name = "pkg"
version = "2.0.0"
files = [
    {file = "pkg-2.0.0.whl", hash = "sha256:2222222222222222222222222222222222222222222222222222222222222222"},
]
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(lock_content, encoding="utf-8")
    with pytest.raises(ValueError, match="Version forks require a target-aware export"):
        export_hash_locked_requirements(lock_file)
