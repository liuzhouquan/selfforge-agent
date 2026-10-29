import hashlib
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from selfforge_agent import cli
from selfforge_agent.runtime import images
from selfforge_agent.verification.runner import DockerTestRunner


def wheelhouse(root: Path) -> tuple[Path, Path]:
    wheels = root / "wheels"
    wheels.mkdir()
    wheel = wheels / "selfforge_fixture_dep-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("selfforge_fixture_dep/__init__.py", "VALUE = 42\n")
        archive.writestr("selfforge_fixture_dep-1.0.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: selfforge-fixture-dep\nVersion: 1.0\n")
        archive.writestr("selfforge_fixture_dep-1.0.dist-info/WHEEL",
                         "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("selfforge_fixture_dep-1.0.dist-info/RECORD", "")
    lock = root / "requirements.lock"
    lock.write_text("selfforge-fixture-dep==1.0 --hash=sha256:" +
                    hashlib.sha256(wheel.read_bytes()).hexdigest() + "\n")
    return lock, wheels


@pytest.mark.parametrize("line", [
    "pytest>=8", "pytest==8.3.5", "-r another.lock", "--index-url https://example.com",
    "thing @ https://example.com/thing.whl", "-e .", "", "pytest[testing]==8.3.5",
])
def test_invalid_lock_never_starts_docker(tmp_path: Path, monkeypatch, line: str) -> None:
    lock, wheels = wheelhouse(tmp_path)
    lock.write_text(line)
    monkeypatch.setattr(images, "_docker", lambda *_args: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError):
        images.build_dependency_image(lock, wheels, tag="test:locked")


@pytest.mark.parametrize("problem", ["changed", "missing", "symlink", "oversized"])
def test_invalid_wheels_never_start_docker(tmp_path: Path, monkeypatch, problem: str) -> None:
    lock, wheels = wheelhouse(tmp_path)
    wheel = next(wheels.glob("*.whl"))
    if problem == "changed":
        wheel.write_bytes(b"tampered")
    elif problem == "missing":
        wheel.unlink()
    elif problem == "symlink":
        target = tmp_path / "external.whl"
        wheel.rename(target)
        wheel.symlink_to(target)
    else:
        monkeypatch.setattr(images, "MAX_WHEEL_BYTES", 1)
    monkeypatch.setattr(images, "_docker", lambda *_args: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError):
        images.build_dependency_image(lock, wheels, tag="test:locked")


@pytest.mark.parametrize("failed", [False, True])
def test_build_uses_frozen_inputs_no_network_and_cleans_only_alias(
    tmp_path: Path, monkeypatch, failed: bool,
) -> None:
    lock, wheels = wheelhouse(tmp_path)
    (wheels / ".env").write_text("must never enter context")
    calls = []
    base_id, image_id = "sha256:" + "a" * 64, "sha256:" + "b" * 64

    def docker(args, timeout, payload=None):
        assert timeout > 0
        calls.append(args)
        if args[0] == "build":
            assert "--network=none" in args and "--pull=false" in args and args[-1] == "-"
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                assert set(archive.getnames()) == {
                    "Dockerfile", "requirements.lock",
                    "wheels/selfforge_fixture_dep-1.0-py3-none-any.whl",
                }
                recipe = archive.extractfile("Dockerfile").read().decode()
                assert "--require-hashes" in recipe and "--only-binary=:all:" in recipe
                assert "--no-index" in recipe and "pip check" in recipe
                assert "PYTHONPATH=/workspace/src:/workspace" in recipe
                assert archive.extractfile("requirements.lock").read() == lock.read_bytes()
            if failed:
                raise ValueError("build failed")
            Path(args[args.index("--iidfile") + 1]).write_text(image_id)
        return base_id if len(calls) == 1 else image_id

    monkeypatch.setattr(images, "_docker", docker)
    if failed:
        with pytest.raises(ValueError, match="build failed"):
            images.build_dependency_image(lock, wheels, tag="project:locked")
    else:
        report = images.build_dependency_image(lock, wheels, tag="project:locked")
        assert report["base_image_id"] == base_id and report["image_id"] == image_id
        assert report["requirements_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
        assert calls[-2][-1] == image_id  # Resolve the build output, not a mutable tag.
    assert calls[1][:2] == ["tag", base_id]
    assert calls[-1] == ["image", "rm", calls[1][2]]
    assert calls[1][2].startswith("selfforge-build-base-")


def test_build_deadline_expiry_stops_before_docker(tmp_path: Path, monkeypatch) -> None:
    lock, wheels = wheelhouse(tmp_path)
    times = iter([0, 3])
    monkeypatch.setattr(images.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(images, "_docker", lambda *_args: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="timed out"):
        images.build_dependency_image(lock, wheels, tag="project:locked", timeout=1)


def test_cli_build_passes_explicit_inputs_and_prints_metadata(tmp_path: Path, monkeypatch, capsys):
    lock, wheels = wheelhouse(tmp_path)
    def build(requirements, wheelhouse, **kwargs):
        assert requirements == lock and wheelhouse == wheels
        assert kwargs == {"tag": "project:locked", "base_image": "base:local", "timeout": 30}
        return {"image_id": "sha256:" + "b" * 64}
    monkeypatch.setattr(cli, "build_dependency_image", build)
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "sandbox", "build", "--requirements",
                                      str(lock), "--wheelhouse", str(wheels), "--tag",
                                      "project:locked", "--base-image", "base:local",
                                      "--timeout", "30"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["image_id"] == "sha256:" + "b" * 64


@pytest.mark.integration
def test_real_offline_image_supplies_dependency_for_src_layout(tmp_path: Path) -> None:
    from uuid import uuid4

    for probe in (["docker", "info"], ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    lock, wheels = wheelhouse(tmp_path)
    tag = "selfforge-dependency-test:" + uuid4().hex
    try:
        report = images.build_dependency_image(lock, wheels, tag=tag, timeout=90)
        repo = tmp_path / "project"
        (repo / "src").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "src/app.py").write_text("from selfforge_fixture_dep import VALUE\n")
        (repo / "tests/test_app.py").write_text(
            "from app import VALUE\ndef test_value():\n    assert VALUE == 42\n")
        evidence = DockerTestRunner(report["image_id"]).run(repo, timeout=20)
        assert evidence.status == "passed" and evidence.passed == 1, evidence
    finally:
        subprocess.run(["docker", "image", "rm", tag], capture_output=True, timeout=10, check=False)
