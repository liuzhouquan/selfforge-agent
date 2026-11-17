"""Security and lifecycle hardening reproduction tests for dependency image builder (SF-IMG-01)."""

import hashlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.runtime import images
from selfforge_agent.runtime.images import (
    _docker,
    _read,
    build_dependency_image,
    parse_locked_requirements,
    scan_wheelhouse,
    validate_image_tag,
)


def _make_wheel(path: Path, name: str, version: str) -> tuple[Path, str]:
    wheel_path = path / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "VALUE = 1\n")
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(
            f"{name}-{version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{name}-{version}.dist-info/RECORD", "")
    digest = "sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    return wheel_path, digest


class TestReadAndParseLockSecurity:
    """SF-IMG-01-A1: Low-level file reading and locked requirement parsing defenses."""

    def test_read_rejects_non_path(self):
        for invalid in [None, 12345, "not_a_path", [Path("/tmp")]]:
            with pytest.raises((TypeError, ValueError)):
                _read(invalid, 1024)

    def test_read_rejects_invalid_limit(self, tmp_path):
        f = tmp_path / "sample.txt"
        f.write_text("hello")
        for invalid in [0, -10, "100", None, float("inf")]:
            with pytest.raises((TypeError, ValueError)):
                _read(f, invalid)

    def test_read_rejects_symlinked_parent_directory(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        target_file = real_dir / "target.txt"
        target_file.write_text("secret")

        sym_dir = tmp_path / "sym_dir"
        sym_dir.symlink_to(real_dir, target_is_directory=True)
        sym_file_path = sym_dir / "target.txt"

        with pytest.raises(ValueError, match="[Ss]ymlink"):
            _read(sym_file_path, 1024)

    def test_read_rejects_fifo(self, tmp_path):
        fifo_path = tmp_path / "test.fifo"
        try:
            os.mkfifo(fifo_path)
        except (AttributeError, OSError):
            pytest.skip("FIFO not supported on this OS")

        with pytest.raises(ValueError):
            _read(fifo_path, 1024)

    def test_parse_locked_requirements_rejects_non_bytes(self):
        for invalid in [None, "requests==2.31.0", 123, ["reqs"]]:
            with pytest.raises((TypeError, ValueError)):
                parse_locked_requirements(invalid)

    def test_parse_locked_requirements_rejects_invalid_utf8(self):
        with pytest.raises(ValueError, match="[Uu]tf-8|[Ee]ncoding|[Ii]nvalid"):
            parse_locked_requirements(b"\xff\xfe\x00\x00not utf8")

    def test_parse_locked_requirements_rejects_null_byte(self):
        with pytest.raises(ValueError):
            parse_locked_requirements(b"pkg==1.0\0 --hash=sha256:" + b"0" * 64)

    def test_parse_locked_requirements_rejects_empty_or_whitespace(self):
        for invalid in [b"", b"   \n  \n", b"# only comment\n# second comment\n"]:
            with pytest.raises(ValueError, match="[Ll]ock must contain|[Ee]mpty"):
                parse_locked_requirements(invalid)

    def test_parse_locked_requirements_rejects_missing_hash_or_markers(self):
        for invalid in [
            b"pkg==1.0\n",
            b"pkg>=1.0 --hash=sha256:" + b"a" * 64 + b"\n",
            b"pkg==1.0; python_version >= '3.10' --hash=sha256:" + b"a" * 64 + b"\n",
            b"pkg[extra]==1.0 --hash=sha256:" + b"a" * 64 + b"\n",
        ]:
            with pytest.raises(ValueError):
                parse_locked_requirements(invalid)


class TestWheelhouseScanSecurity:
    """SF-IMG-01-A2: Wheelhouse directory scanning, deadline checking, and wheel verification."""

    def test_scan_wheelhouse_rejects_non_path_wheelhouse(self):
        for invalid in [None, 12345, "not_a_path", [Path("/tmp")]]:
            with pytest.raises((TypeError, ValueError)):
                scan_wheelhouse(b"lock", [], invalid, 1e9)

    def test_scan_wheelhouse_rejects_symlinked_wheelhouse_or_parent(self, tmp_path):
        real_dir = tmp_path / "real_wheels"
        real_dir.mkdir()
        sym_dir = tmp_path / "sym_wheels"
        sym_dir.symlink_to(real_dir, target_is_directory=True)

        with pytest.raises(ValueError, match="[Ss]ymlink"):
            scan_wheelhouse(b"lock", [], sym_dir, 1e9)

    def test_scan_wheelhouse_rejects_invalid_deadline(self, tmp_path):
        wheelhouse_dir = tmp_path / "wheels"
        wheelhouse_dir.mkdir()
        for invalid in [0.0, -5.0, float("nan"), float("inf"), None, "deadline"]:
            with pytest.raises((TypeError, ValueError)):
                scan_wheelhouse(b"lock", [], wheelhouse_dir, invalid)

    def test_scan_wheelhouse_rejects_empty_wheel_file(self, tmp_path):
        wheelhouse_dir = tmp_path / "wheels"
        wheelhouse_dir.mkdir()
        empty_wheel = wheelhouse_dir / "pkg-1.0-py3-none-any.whl"
        empty_wheel.write_bytes(b"")

        packages = [{"name": "pkg", "version": "1.0", "hashes": ["sha256:" + "0" * 64]}]
        with pytest.raises(ValueError, match="[Ee]mpty|[Ss]ize|[Hh]ash"):
            scan_wheelhouse(b"lock", packages, wheelhouse_dir, 1e9)

    def test_scan_wheelhouse_rejects_non_regular_file_in_wheelhouse(self, tmp_path):
        wheelhouse_dir = tmp_path / "wheels"
        wheelhouse_dir.mkdir()
        fake_wheel_dir = wheelhouse_dir / "fake-1.0-py3-none-any.whl"
        fake_wheel_dir.mkdir()

        packages = [{"name": "fake", "version": "1.0", "hashes": ["sha256:" + "0" * 64]}]
        with pytest.raises(ValueError):
            scan_wheelhouse(b"lock", packages, wheelhouse_dir, 1e9)

    def test_scan_wheelhouse_rejects_symlinked_wheel(self, tmp_path):
        wheelhouse_dir = tmp_path / "wheels"
        wheelhouse_dir.mkdir()
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        target_wheel, digest = _make_wheel(external_dir, "pkg", "1.0")

        sym_wheel = wheelhouse_dir / "pkg-1.0-py3-none-any.whl"
        sym_wheel.symlink_to(target_wheel)

        packages = [{"name": "pkg", "version": "1.0", "hashes": [digest]}]
        with pytest.raises(ValueError, match="[Ss]ymlink"):
            scan_wheelhouse(b"lock", packages, wheelhouse_dir, 1e9)

    def test_scan_wheelhouse_rejects_hash_mismatch(self, tmp_path):
        wheelhouse_dir = tmp_path / "wheels"
        wheelhouse_dir.mkdir()
        _make_wheel(wheelhouse_dir, "pkg", "1.0")

        wrong_packages = [{"name": "pkg", "version": "1.0", "hashes": ["sha256:" + "f" * 64]}]
        with pytest.raises(ValueError, match="[Hh]ash"):
            scan_wheelhouse(b"lock", wrong_packages, wheelhouse_dir, 1e9)


class TestImageTagAndDockerInvocationSecurity:
    """SF-IMG-01-A3: Image tag validation and bounded docker process execution."""

    def test_validate_image_tag_rejects_non_string(self):
        for invalid in [None, 12345, ["tag"], {"tag": "val"}]:
            with pytest.raises((TypeError, ValueError)):
                validate_image_tag(invalid)

    def test_validate_image_tag_rejects_empty_or_whitespace(self):
        for invalid in ["", "   ", "\t\n"]:
            with pytest.raises(ValueError):
                validate_image_tag(invalid)

    def test_validate_image_tag_rejects_null_byte(self):
        with pytest.raises(ValueError):
            validate_image_tag("myimage\0tag:latest")

    def test_validate_image_tag_rejects_oversized_tag(self):
        with pytest.raises(ValueError):
            validate_image_tag("a" * 300)

    def test_validate_image_tag_rejects_option_injection(self):
        for invalid in ["--help", "-t", "--build-arg=x=1", "-v"]:
            with pytest.raises(ValueError):
                validate_image_tag(invalid)

    def test_docker_rejects_non_list_or_non_string_args(self):
        for invalid in ["image inspect", 123, None, ["image", 123]]:
            with pytest.raises((TypeError, ValueError)):
                _docker(invalid, 10.0)

    def test_docker_rejects_null_byte_in_args(self):
        with pytest.raises(ValueError):
            _docker(["image\0inspect", "tag"], 10.0)

    def test_docker_rejects_invalid_timeout(self):
        for invalid in [0, -5, None, "30", float("nan"), float("inf")]:
            with pytest.raises((TypeError, ValueError)):
                _docker(["version"], invalid)

    def test_docker_maps_missing_binary_to_value_error(self, monkeypatch):
        def bad_run(*args, **kwargs):
            raise FileNotFoundError("No docker executable")

        monkeypatch.setattr("subprocess.run", bad_run)
        with pytest.raises(ValueError, match="[Dd]ocker.*[Nn]ot installed|[Nn]ot found"):
            _docker(["version"], 10.0)


class TestBuildDependencyImageGatingAndIntegrity:
    """SF-IMG-01-A4: build_dependency_image parameter defense, timeout gates, and cleanup integrity."""

    def test_build_dependency_image_rejects_non_path_inputs(self):
        for invalid in [None, "requirements.lock", 123]:
            with pytest.raises((TypeError, ValueError)):
                build_dependency_image(invalid, Path("/tmp"), tag="test:latest")

        for invalid in [None, "wheels", 123]:
            with pytest.raises((TypeError, ValueError)):
                build_dependency_image(Path("/tmp"), invalid, tag="test:latest")

    def test_build_dependency_image_rejects_invalid_timeout(self, tmp_path):
        req = tmp_path / "requirements.lock"
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        req.write_text("pkg==1.0 --hash=sha256:" + "0" * 64 + "\n")

        for invalid in [0, -1, 1801, None, "300", float("nan"), float("inf"), True]:
            with pytest.raises((TypeError, ValueError)):
                build_dependency_image(req, wheels, tag="test:latest", timeout=invalid)

    def test_build_dependency_image_rejects_invalid_base_image(self, tmp_path):
        req = tmp_path / "requirements.lock"
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        req.write_text("pkg==1.0 --hash=sha256:" + "0" * 64 + "\n")

        for invalid in ["", "  ", "--option-injection", "image with space", "image\0null", "a" * 300]:
            with pytest.raises(ValueError):
                build_dependency_image(req, wheels, tag="test:latest", base_image=invalid)

    def test_build_dependency_image_cleans_alias_on_failure(self, tmp_path, monkeypatch):
        wheel_dir = tmp_path / "wheels"
        wheel_dir.mkdir()
        _wheel_path, digest = _make_wheel(wheel_dir, "pkg", "1.0")

        req = tmp_path / "requirements.lock"
        req.write_text(f"pkg==1.0 --hash={digest}\n")

        docker_calls = []
        base_id = "sha256:" + "a" * 64

        def mock_docker(args, timeout, payload=None):
            docker_calls.append(args)
            if args[0] == "build":
                raise ValueError("Simulated docker build failure")
            return base_id

        monkeypatch.setattr(images, "_docker", mock_docker)

        with pytest.raises(ValueError, match="Simulated docker build failure"):
            build_dependency_image(req, wheel_dir, tag="mybuild:latest")

        # Verify that alias tag was created and subsequently removed in finally block
        alias_tags = [c[2] for c in docker_calls if c[0] == "tag"]
        assert len(alias_tags) == 1
        created_alias = alias_tags[0]

        rm_calls = [c for c in docker_calls if c[0] == "image" and c[1] == "rm"]
        assert len(rm_calls) == 1
        assert rm_calls[0][2] == created_alias
