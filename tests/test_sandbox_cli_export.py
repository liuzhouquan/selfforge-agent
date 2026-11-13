"""Tests for SF-ENV-07: CLI sandbox structured export and output write hardening."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from selfforge_agent.cli import main
from selfforge_agent.runtime.environment import export_hash_locked_requirements


def _setup_sample_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "demo").mkdir()
    (repo / "src" / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_dummy.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click>=8.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        "click==8.1.7 --hash=sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    return repo


def test_sandbox_detect_json_and_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_sample_repo(tmp_path)

    # 1. Default text output (backward compatibility)
    exit_code = main(["sandbox", "detect", "--repo", str(repo)])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Repository:" in captured
    assert "Layout: src" in captured
    assert "Test framework: pytest" in captured

    # 2. --json flag produces structured JSON to stdout
    exit_code = main(["sandbox", "detect", "--repo", str(repo), "--json"])
    assert exit_code == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["layout"] == "src"
    assert data["test_framework"] == "pytest"
    assert "requirements.lock" in data["lock_files"]
    assert data["has_hash_lock"] is True

    # 3. --output writes structured JSON safely to regular file
    out_file = tmp_path / "detected.json"
    exit_code = main(["sandbox", "detect", "--repo", str(repo), "--output", str(out_file)])
    assert exit_code == 0
    assert out_file.is_file()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["layout"] == "src"


def test_sandbox_plan_json_and_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_sample_repo(tmp_path)

    # 1. --json outputs plan as JSON to stdout
    exit_code = main(["sandbox", "plan", "--repo", str(repo), "--json"])
    assert exit_code == 0
    captured_json = capsys.readouterr().out
    plan = json.loads(captured_json)
    assert "tag" in plan
    assert "safety_invariants" in plan

    # 2. --output writes plan safely to file
    out_file = tmp_path / "plan.json"
    exit_code = main(["sandbox", "plan", "--repo", str(repo), "--output", str(out_file)])
    assert exit_code == 0
    assert out_file.is_file()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert "tag" in saved


def test_sandbox_cli_outputs_fail_closed_against_symlinks_fifos_and_dirs(
    tmp_path: Path,
) -> None:
    repo = _setup_sample_repo(tmp_path)

    real_victim = tmp_path / "victim.txt"
    real_victim.write_text("DO_NOT_OVERWRITE", encoding="utf-8")

    symlink_target = tmp_path / "symlink_out.json"
    symlink_target.symlink_to(real_victim)

    dir_target = tmp_path / "dir_out"
    dir_target.mkdir()

    missing_parent_target = tmp_path / "nonexistent_dir" / "out.json"

    fifo_target = tmp_path / "out.fifo"
    os.mkfifo(fifo_target)

    bad_targets = (symlink_target, dir_target, missing_parent_target, fifo_target)

    for bad in bad_targets:
        # detect
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "detect", "--repo", str(repo), "--output", str(bad)])
        assert exc.value.code == 2

        # plan
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "plan", "--repo", str(repo), "--output", str(bad)])
        assert exc.value.code == 2

        # audit
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir(exist_ok=True)
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "audit", "--repo", str(repo), "--wheelhouse", str(wheelhouse), "--output", str(bad)])
        assert exc.value.code == 2

        # freshness
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "freshness", "--repo", str(repo), "--output", str(bad)])
        assert exc.value.code == 2

        # check (alias of freshness)
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "check", "--repo", str(repo), "--output", str(bad)])
        assert exc.value.code == 2

        # export-lock
        lock_file = repo / "requirements.lock"
        with pytest.raises(SystemExit) as exc:
            main(["sandbox", "export-lock", "--lock", str(lock_file), "--output", str(bad)])
        assert exc.value.code == 2

    # Victim file was NEVER overwritten
    assert real_victim.read_text(encoding="utf-8") == "DO_NOT_OVERWRITE"


def test_export_hash_locked_requirements_rejects_symlink_and_special_files(
    tmp_path: Path,
) -> None:
    repo = _setup_sample_repo(tmp_path)
    lock_file = repo / "requirements.lock"

    victim = tmp_path / "victim2.txt"
    victim.write_text("DO_NOT_TOUCH", encoding="utf-8")

    symlink_out = tmp_path / "sym_export.txt"
    symlink_out.symlink_to(victim)

    dir_out = tmp_path / "dir_export"
    dir_out.mkdir()

    fifo_out = tmp_path / "fifo_export"
    os.mkfifo(fifo_out)

    with pytest.raises(ValueError, match="symlink"):
        export_hash_locked_requirements(lock_file, symlink_out)

    with pytest.raises(ValueError, match="regular file"):
        export_hash_locked_requirements(lock_file, dir_out)

    with pytest.raises(ValueError, match="regular file"):
        export_hash_locked_requirements(lock_file, fifo_out)

    assert victim.read_text(encoding="utf-8") == "DO_NOT_TOUCH"


@pytest.mark.parametrize('action', ['freshness', 'check', 'plan', 'audit', 'export-lock'])
@pytest.mark.parametrize('with_output', [False, True])
def test_sandbox_json_flag_always_produces_json_stdout(tmp_path, capsys, action, with_output):
    repo = _setup_sample_repo(tmp_path)
    args = ['sandbox', action, '--repo', str(repo), '--json']
    if action == 'audit':
        wheels = tmp_path / 'wheels'
        wheels.mkdir()
        args += ['--wheelhouse', str(wheels)]
    if action == 'export-lock':
        args += ['--lock', str(repo / 'requirements.lock')]
    if with_output:
        args += ['--output', str(tmp_path / 'result')]
    assert main(args) in (0, 1)
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result, dict)
    if action == 'export-lock':
        assert 'click==8.1.7' in result['requirements']
