"""Structured-lock public boundaries must preserve strict offline preparation."""
from pathlib import Path

import pytest

from selfforge_agent.runtime.environment import (
    detect_repository_environment,
    export_hash_locked_requirements,
    plan_environment_preparation,
)


def poetry(*, prefix="", digest="a" * 64, extra=""):
    return prefix + '\n[[package]]\nname = "demo"\nversion = "1"\n' + extra + '\nfiles = [{file="demo.whl", hash="sha256:' + digest + '"}]\n'


def test_poetry_rejects_malformed_metadata_at_api_boundary(tmp_path):
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app
    (tmp_path / "poetry.lock").write_text(poetry(prefix="metadata=[]"))
    response = TestClient(app).post("/api/sandbox/plan", json={"repository": str(tmp_path)})
    assert response.status_code == 400
    assert "metadata" in response.json()["detail"]


def test_poetry_markers_are_not_erased(tmp_path):
    p = tmp_path / "poetry.lock"
    p.write_text(poetry(extra='markers = "sys_platform == \'win32\'"'))
    with pytest.raises(ValueError, match="target-aware"):
        export_hash_locked_requirements(p)


def test_uppercase_structured_hashes_produce_usable_plan(tmp_path):
    p = tmp_path / "poetry.lock"
    p.write_text(poetry(digest="A" * 64))
    assert "sha256:" + "a" * 64 in export_hash_locked_requirements(p)
    assert plan_environment_preparation(tmp_path)["package_count"] == 1


def test_discovery_finds_hash_after_first_chunk(tmp_path):
    (tmp_path / "poetry.lock").write_text(poetry(prefix="#" + "x" * 140_000))
    assert detect_repository_environment(tmp_path)["has_hash_lock"]


def test_flat_lock_name_is_not_a_format_selector(tmp_path):
    p = tmp_path / "uvicorn-requirements.lock"
    p.write_text("demo==1 --hash=sha256:" + "a" * 64)
    plan = plan_environment_preparation(tmp_path, lock_file=p)
    assert plan["package_count"] == 1
    assert plan["export_command"] is None


def test_format_detection_does_not_read_symlink(tmp_path, monkeypatch):
    p = tmp_path / "profile.lock"
    target = tmp_path / "target"
    target.write_text("demo==1 --hash=sha256:" + "a" * 64)
    p.symlink_to(target)
    opened = []
    original = Path.open
    def record(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", record)
    with pytest.raises(ValueError, match="symlink"):
        plan_environment_preparation(tmp_path, lock_file=p)
    assert p not in opened and target not in opened


def pipfile(tmp_path, *, extra=None, version="==1", default=None):
    import json
    package = {"version": version, "hashes": ["sha256:" + "a" * 64], **(extra or {})}
    payload = {"default": {"demo": package}} if default is None else {"default": default, "develop": {"demo": package}}
    p = tmp_path / "Pipfile.lock"
    p.write_text(json.dumps(payload))
    return p


def test_pipfile_does_not_drop_invalid_sections(tmp_path):
    p = pipfile(tmp_path, default=["invalid"])
    with pytest.raises(ValueError, match="section"):
        export_hash_locked_requirements(p)


def test_pipfile_does_not_erase_markers(tmp_path):
    p = pipfile(tmp_path, extra={"markers": "sys_platform == 'win32'"})
    with pytest.raises(ValueError, match="target-aware"):
        export_hash_locked_requirements(p)


@pytest.mark.parametrize("version", ["1", "=1", "===1"])
def test_pipfile_requires_exact_pin_syntax(tmp_path, version):
    p = pipfile(tmp_path, version=version)
    with pytest.raises(ValueError, match="version"):
        export_hash_locked_requirements(p)


def test_pipfile_duplicate_keys_are_not_silently_overwritten(tmp_path):
    p = pipfile(tmp_path)
    text = p.read_text()
    text = text.replace('{"default":', '{"default": {}, "default":', 1)
    p.write_text(text)
    with pytest.raises(ValueError, match="Duplicate"):
        export_hash_locked_requirements(p)


def test_pipfile_uppercase_hashes_work_through_real_api(tmp_path):
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app
    pipfile(tmp_path, extra={"hashes": ["sha256:" + "A" * 64]})
    response = TestClient(app).post("/api/sandbox/plan", json={"repository": str(tmp_path)})
    assert response.status_code == 200
    assert response.json()["packages"][0]["hashes"] == ["sha256:" + "a" * 64]



def test_deep_pipfile_json_is_a_client_error(tmp_path):
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app
    (tmp_path / "Pipfile.lock").write_text('{"default":' + '[' * 1200 + '0' + ']' * 1200 + '}')
    response = TestClient(app).post("/api/sandbox/plan", json={"repository": str(tmp_path)})
    assert response.status_code == 400
