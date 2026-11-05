"""Tests for wheelhouse audit and readiness verification (SF-ENV-05)."""

import hashlib
import json
from pathlib import Path

import pytest

from selfforge_agent.runtime.environment import audit_wheelhouse


def _make_wheel(name: str, content: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    return name, digest


def test_audit_wheelhouse_all_matched_ready(tmp_path: Path) -> None:
    click_content = b"click-wheel-binary-content"
    six_content = b"six-wheel-binary-content"
    _, click_sha = _make_wheel("click", click_content)
    _, six_sha = _make_wheel("six", six_content)

    req_text = (
        f"click==8.1.7 --hash=sha256:{click_sha}\n"
        f"six==1.16.0 --hash=sha256:{six_sha}\n"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text(req_text, encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(click_content)
    (wheelhouse / "six-1.16.0-py2.py3-none-any.whl").write_bytes(six_content)

    report = audit_wheelhouse(repo, wheelhouse=wheelhouse)
    assert report["ready"] is True
    assert report["package_count"] == 2
    assert report["matched_count"] == 2
    assert report["missing_count"] == 0
    assert report["mismatched_count"] == 0
    assert report["unsupported_count"] == 0
    assert len(report["matched_packages"]) == 2
    assert report["missing_packages"] == []
    assert report["mismatched_wheels"] == []
    assert report["unsupported_wheels"] == []
    assert report["total_wheel_bytes"] == len(click_content) + len(six_content)


def test_audit_wheelhouse_missing_package(tmp_path: Path) -> None:
    click_content = b"click-wheel-binary-content"
    _, click_sha = _make_wheel("click", click_content)
    six_sha = "66" * 32

    req_text = (
        f"click==8.1.7 --hash=sha256:{click_sha}\n"
        f"six==1.16.0 --hash=sha256:{six_sha}\n"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text(req_text, encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(click_content)

    report = audit_wheelhouse(repo, wheelhouse=wheelhouse)
    assert report["ready"] is False
    assert report["package_count"] == 2
    assert report["matched_count"] == 1
    assert report["missing_count"] == 1
    assert report["missing_packages"][0]["name"] == "six"
    assert report["missing_packages"][0]["version"] == "1.16.0"
    assert report["missing_packages"][0]["hashes"] == [f"sha256:{six_sha}"]


def test_audit_wheelhouse_mismatched_and_unsupported_wheels(tmp_path: Path) -> None:
    click_content = b"click-wheel-binary-content"
    extra_content = b"extra-unlocked-wheel-content"
    _, click_sha = _make_wheel("click", click_content)
    _, extra_sha = _make_wheel("extra", extra_content)

    req_text = f"click==8.1.7 --hash=sha256:{click_sha}\n"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text(req_text, encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(click_content)
    (wheelhouse / "extra_pkg-1.0-py3-none-any.whl").write_bytes(extra_content)
    (wheelhouse / "illegal name!.whl").write_bytes(b"bad name content")

    report = audit_wheelhouse(repo, wheelhouse=wheelhouse)
    assert report["ready"] is False
    assert report["matched_count"] == 1
    assert report["mismatched_count"] == 1
    assert report["mismatched_wheels"][0]["filename"] == "extra_pkg-1.0-py3-none-any.whl"
    assert report["mismatched_wheels"][0]["hash"] == f"sha256:{extra_sha}"
    assert report["unsupported_count"] == 1
    assert report["unsupported_wheels"][0]["filename"] == "illegal name!.whl"


def test_audit_wheelhouse_symlink_directory_rejected(tmp_path: Path) -> None:
    target_dir = tmp_path / "target_wheelhouse"
    target_dir.mkdir()
    symlink_dir = tmp_path / "symlink_wheelhouse"
    symlink_dir.symlink_to(target_dir)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text("click==8.1.7 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Wheelhouse must be a directory, not a symlink"):
        audit_wheelhouse(repo, wheelhouse=symlink_dir)


def test_audit_wheelhouse_nonexistent_directory_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text("click==8.1.7 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not exist"):
        audit_wheelhouse(repo, wheelhouse=tmp_path / "nonexistent")


def test_audit_wheelhouse_with_explicit_lock_file(tmp_path: Path) -> None:
    six_content = b"six-wheel-content"
    _, six_sha = _make_wheel("six", six_content)

    pipfile_data = {
        "default": {
            "six": {
                "version": "==1.16.0",
                "hashes": [f"sha256:{six_sha}"],
            },
        },
    }
    lock_file = tmp_path / "custom_pipfile.lock"
    lock_file.write_text(json.dumps(pipfile_data), encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "six-1.16.0-py2.py3-none-any.whl").write_bytes(six_content)

    report = audit_wheelhouse(wheelhouse=wheelhouse, lock_file=lock_file)
    assert report["ready"] is True
    assert report["package_count"] == 1
    assert report["matched_count"] == 1
    assert report["matched_packages"][0]["name"] == "six"


def test_cli_sandbox_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    from selfforge_agent.cli import main

    click_content = b"click-wheel-binary"
    _, click_sha = _make_wheel("click", click_content)

    req_text = f"click==8.1.7 --hash=sha256:{click_sha}\n"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text(req_text, encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(click_content)

    output_file = tmp_path / "audit_report.json"
    code = main(["sandbox", "audit", "--repo", str(repo), "--wheelhouse", str(wheelhouse), "--output", str(output_file)])
    assert code == 0
    assert output_file.is_file()
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved["ready"] is True
    assert saved["matched_count"] == 1

    # Incomplete wheelhouse returns exit code 1
    empty_wheelhouse = tmp_path / "empty_wheelhouse"
    empty_wheelhouse.mkdir()
    code_fail = main(["sandbox", "audit", "--repo", str(repo), "--wheelhouse", str(empty_wheelhouse)])
    assert code_fail == 1


def test_api_sandbox_audit_endpoint(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app

    client = TestClient(app)
    click_content = b"click-wheel-binary"
    _, click_sha = _make_wheel("click", click_content)

    req_text = f"click==8.1.7 --hash=sha256:{click_sha}\n"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.lock").write_text(req_text, encoding="utf-8")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "click-8.1.7-py3-none-any.whl").write_bytes(click_content)

    res = client.post("/api/sandbox/audit", json={
        "repository": str(repo),
        "wheelhouse": str(wheelhouse),
    })
    assert res.status_code == 200
    report = res.json()
    assert report["ready"] is True
    assert report["package_count"] == 1
    assert report["matched_count"] == 1

