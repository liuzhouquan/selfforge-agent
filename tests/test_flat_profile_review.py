from pathlib import Path

import pytest

from selfforge_agent.runtime.environment import (
    export_hash_locked_requirements,
    plan_environment_preparation,
)


@pytest.mark.parametrize("mode", ["default", "lock", "requirements"])
def test_flat_uppercase_profile_works_in_plan(tmp_path: Path, mode):
    p = tmp_path / "requirements.lock"
    p.write_text("demo==1 --hash=SHA256:" + "A" * 64)
    kwargs = {"lock_file": p} if mode == "lock" else {"requirements_file": p} if mode == "requirements" else {}
    plan = plan_environment_preparation(tmp_path, **kwargs)
    assert plan["packages"][0]["hashes"] == ["sha256:" + "a" * 64]
    assert plan["export_command"]


def test_flat_epoch_version_is_preserved(tmp_path):
    p = tmp_path / "requirements.lock"
    p.write_text("demo==1!2.0 --hash=sha256:" + "a" * 64)
    assert "demo==1!2.0" in export_hash_locked_requirements(p)


def test_invalid_hash_cannot_be_erased_alongside_valid_hash(tmp_path):
    p = tmp_path / "requirements.lock"
    p.write_text("demo==1 --hash=sha256:" + "a" * 64 + " --hash=md5:invalid")
    with pytest.raises(ValueError):
        export_hash_locked_requirements(p)
