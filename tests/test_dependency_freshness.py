"""Tests for dependency declaration and lockfile freshness and dry-run diagnosis (SF-ENV-06)."""

import hashlib
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.cli import main
from selfforge_agent.runtime.environment import (
    check_dependency_freshness,
    plan_environment_preparation,
)


def _make_hash(content: str = "wheel-bytes") -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_freshness_clean_consistent_repo(tmp_path: Path) -> None:
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    h1 = _make_hash("requests")
    h2 = _make_hash("click")

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["requests>=2.28.0", "click>=8.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n"
        f"click==8.1.7 --hash=sha256:{h2}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is True
    assert result["status"] == "fresh"
    assert result["declared_count"] == 2
    assert result["missing_packages"] == []
    assert result["incompatible_packages"] == []
    assert result["is_stale_by_mtime"] is False


def test_freshness_missing_dependency(tmp_path: Path) -> None:
    repo = tmp_path / "missing_repo"
    repo.mkdir()
    h1 = _make_hash("requests")

    # pyproject declares requests and fastapi, but lockfile only has requests
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["requests>=2.28.0", "fastapi>=0.100.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is False
    assert result["status"] == "missing_dependencies"
    assert len(result["missing_packages"]) == 1
    assert result["missing_packages"][0]["name"] == "fastapi"
    assert any("fastapi" in w for w in result["warnings"])


def test_freshness_incompatible_version(tmp_path: Path) -> None:
    repo = tmp_path / "incompatible_repo"
    repo.mkdir()
    h1 = _make_hash("requests")

    # pyproject requires requests>=3.0.0, but lockfile is locked to 2.31.0
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["requests>=3.0.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is False
    assert result["status"] == "incompatible_versions"
    assert len(result["incompatible_packages"]) == 1
    incomp = result["incompatible_packages"][0]
    assert incomp["name"] == "requests"
    assert incomp["locked_version"] == "2.31.0"
    assert incomp["declared_specifier"] == ">=3.0.0"


def test_freshness_mtime_drift(tmp_path: Path) -> None:
    repo = tmp_path / "mtime_repo"
    repo.mkdir()
    h1 = _make_hash("requests")

    lock_file = repo / "requirements.lock"
    lock_file.write_text(f"requests==2.31.0 --hash=sha256:{h1}\n", encoding="utf-8")
    # Lockfile mtime set to past
    now = time.time()
    os.utime(lock_file, (now - 100, now - 100))

    decl_file = repo / "pyproject.toml"
    decl_file.write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["requests>=2.0.0"]\n',
        encoding="utf-8",
    )
    # Declaration mtime set to more recent
    os.utime(decl_file, (now, now))

    result = check_dependency_freshness(repo)
    assert result["is_stale_by_mtime"] is True
    assert any("newer than" in w for w in result["warnings"])


def test_freshness_pep508_markers_filtered(tmp_path: Path) -> None:
    repo = tmp_path / "marker_repo"
    repo.mkdir()
    h1 = _make_hash("click")

    # importlib-metadata is only for python < 3.8; on python >= 3.8 it is skipped
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["click>=8.0.0", "importlib-metadata>=4.0; python_version < \'3.8\'"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"click==8.1.7 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    # importlib-metadata is safely skipped for current python environment
    assert result["fresh"] is True
    assert result["missing_packages"] == []


def test_freshness_poetry_specifiers_converted(tmp_path: Path) -> None:
    repo = tmp_path / "poetry_repo"
    repo.mkdir()
    h1 = _make_hash("requests")

    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "myproj"\nversion = "0.1.0"\n'
        '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2.28.0"\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is True
    assert result["declared_count"] == 1  # python skipped, requests counted
    assert result["missing_packages"] == []
    assert result["incompatible_packages"] == []


def test_freshness_requirements_in_supported(tmp_path: Path) -> None:
    repo = tmp_path / "req_in_repo"
    repo.mkdir()
    h1 = _make_hash("flask")

    (repo / "requirements.in").write_text("flask>=2.0.0\n", encoding="utf-8")
    (repo / "requirements.lock").write_text(
        f"flask==2.3.2 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is True
    assert result["declaration_file"] == str(repo / "requirements.in")
    assert result["declared_count"] == 1


def test_freshness_pipfile_supported(tmp_path: Path) -> None:
    repo = tmp_path / "pipfile_repo"
    repo.mkdir()
    h1 = _make_hash("urllib3")

    (repo / "Pipfile").write_text(
        '[[source]]\nurl = "https://pypi.org/simple"\nverify_ssl = true\nname = "pypi"\n\n'
        '[packages]\nurllib3 = ">=1.26"\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"urllib3==2.0.7 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    result = check_dependency_freshness(repo)
    assert result["fresh"] is True
    assert result["declared_count"] == 1


def test_freshness_no_declaration_file_graceful(tmp_path: Path) -> None:
    repo = tmp_path / "no_decl_repo"
    repo.mkdir()
    h1 = _make_hash("simple")

    (repo / "requirements.lock").write_text(f"simple==1.0.0 --hash=sha256:{h1}\n", encoding="utf-8")

    result = check_dependency_freshness(repo)
    assert result["fresh"] is True
    assert result["status"] == "no_declarations"
    assert result["declared_count"] == 0
    assert result["declaration_file"] is None


def test_plan_environment_preparation_integrates_freshness(tmp_path: Path) -> None:
    repo = tmp_path / "plan_freshness_repo"
    repo.mkdir()
    h1 = _make_hash("pkg_a")

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "0.1.0"\n'
        'dependencies = ["pkg-a>=1.0.0", "pkg-b>=2.0.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"pkg-a==1.2.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    plan = plan_environment_preparation(repo)
    assert "freshness" in plan
    freshness = plan["freshness"]
    assert freshness["fresh"] is False
    assert any("pkg-b" in m["name"] for m in freshness["missing_packages"])
    assert any("Lockfile is stale or missing declared dependencies" in w for w in plan["warnings"])


def test_cli_sandbox_freshness_exit_codes_and_output(tmp_path: Path, capsys) -> None:
    h1 = _make_hash("requests")

    # Clean repo
    clean_repo = tmp_path / "cli_clean"
    clean_repo.mkdir()
    (clean_repo / "pyproject.toml").write_text(
        '[project]\nname = "clean"\nversion = "0.1.0"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    (clean_repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    # Stale repo (missing dependency)
    stale_repo = tmp_path / "cli_stale"
    stale_repo.mkdir()
    (stale_repo / "pyproject.toml").write_text(
        '[project]\nname = "stale"\nversion = "0.1.0"\ndependencies = ["requests>=2.0", "missing-pkg"]\n',
        encoding="utf-8",
    )
    (stale_repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    # Clean repo returns 0
    code_clean = main(["sandbox", "freshness", "--repo", str(clean_repo)])
    assert code_clean == 0
    captured = capsys.readouterr()
    assert "Dependency lock is fresh and consistent" in captured.out

    # Stale repo returns 1
    code_stale = main(["sandbox", "freshness", "--repo", str(stale_repo)])
    assert code_stale == 1
    captured = capsys.readouterr()
    assert "missing-pkg" in captured.out or "missing_dependencies" in captured.out

    # Output file test
    out_file = tmp_path / "report.json"
    code_out = main(["sandbox", "freshness", "--repo", str(clean_repo), "--output", str(out_file)])
    assert code_out == 0
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["fresh"] is True


def test_api_sandbox_freshness_endpoint(tmp_path: Path) -> None:
    repo = tmp_path / "api_repo"
    repo.mkdir()
    h1 = _make_hash("requests")

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "api"\nversion = "0.1.0"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        f"requests==2.31.0 --hash=sha256:{h1}\n",
        encoding="utf-8",
    )

    client = TestClient(api_module.app)
    response = client.post("/api/sandbox/freshness", json={"repository": str(repo)})
    assert response.status_code == 200
    body = response.json()
    assert body["fresh"] is True
    assert body["status"] == "fresh"
    assert body["declared_count"] == 1
