"""Deep security hardening and diagnostic models reproduction tests for dependency images (SF-IMG-02)."""

import hashlib
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.runtime.images import (
    MAX_LOCK_BYTES,
    DependencyImageResult,
    _docker,
    build_dependency_image,
    diagnose_wheelhouse,
    image_exists,
    parse_locked_requirements,
    resolve_image_id,
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


class TestLockfileHashNormalizationAndInputGating:
    """SF-IMG-02-A1: Lockfile parsing hash normalization and fail-closed input defense."""

    def test_uppercase_and_mixed_case_sha256_normalized_to_lowercase(self):
        raw_hash = "A" * 32 + "F" * 32
        lock = f"pkg-alpha==1.0.0 --hash=sha256:{raw_hash}\n".encode()
        packages = parse_locked_requirements(lock)
        assert len(packages) == 1
        assert packages[0]["name"] == "pkg-alpha"
        assert packages[0]["version"] == "1.0.0"
        assert packages[0]["hashes"] == [f"sha256:{raw_hash.lower()}"]

    def test_mixed_case_hash_prefix_and_multiple_hashes_normalized(self):
        h1 = "a1b2c3d4" * 8
        h2 = "E5F6A7B8" * 8
        lock = f"pkg-multi==2.1.0 --hash=SHA256:{h1} --hash=sha256:{h2}\n".encode()
        packages = parse_locked_requirements(lock)
        assert len(packages) == 1
        assert packages[0]["name"] == "pkg-multi"
        assert set(packages[0]["hashes"]) == {f"sha256:{h1.lower()}", f"sha256:{h2.lower()}"}

    def test_duplicate_canonical_package_names_rejected(self):
        h = "0" * 64
        lock = (
            f"foo-bar==1.0.0 --hash=sha256:{h}\n"
            f"foo_bar==1.0.0 --hash=sha256:{h}\n"
        ).encode()
        with pytest.raises(ValueError, match="Duplicate requirement names"):
            parse_locked_requirements(lock)

    def test_reject_empty_or_whitespace_or_comment_only(self):
        for invalid in [b"", b"   \n  \t\n", b"# comment only\n# second\n"]:
            with pytest.raises(ValueError, match="Lock must contain 1-200"):
                parse_locked_requirements(invalid)

    def test_reject_exceeding_200_packages(self):
        lines = [f"pkg-{i:03d}==1.0.0 --hash=sha256:{'0' * 64}" for i in range(201)]
        lock = "\n".join(lines).encode()
        with pytest.raises(ValueError, match="Lock must contain 1-200"):
            parse_locked_requirements(lock)

    def test_reject_lines_with_urls_markers_extras_directives(self):
        h = "0" * 64
        invalid_lines = [
            f"git+https://github.com/example/repo.git#egg=pkg --hash=sha256:{h}",
            f"pkg[extra]==1.0.0 --hash=sha256:{h}",
            f"pkg==1.0.0; python_version>='3.8' --hash=sha256:{h}",
            "-r other.txt",
            "--index-url https://pypi.org/simple",
            f"pkg==1.0.0\0 --hash=sha256:{h}",
        ]
        for line in invalid_lines:
            with pytest.raises(ValueError, match="Lock requires exact|Lockfile must not contain null bytes"):
                parse_locked_requirements(line.encode())

    def test_reject_non_bytes_or_oversized_lockfile(self):
        with pytest.raises(TypeError, match="lock must be bytes"):
            parse_locked_requirements("not bytes")
        with pytest.raises(ValueError, match="size limit exceeded"):
            parse_locked_requirements(b"a" * (MAX_LOCK_BYTES + 1))


class TestWheelhouseScanPackageGatingAndDiagnostics:
    """SF-IMG-02-A2: Wheelhouse scan package gating, hidden file filtering and diagnostic abstraction."""

    def test_scan_wheelhouse_rejects_malformed_packages_list(self, tmp_path):
        deadline = 1e12
        lock = b"sample==1.0 --hash=sha256:" + b"0" * 64
        for malformed in [
            ["not a dict"],
            [{"name": 123, "version": "1.0", "hashes": ["sha256:" + "0" * 64]}],
            [{"name": "pkg", "version": 123, "hashes": ["sha256:" + "0" * 64]}],
            [{"name": "pkg", "version": "1.0", "hashes": "not-a-list"}],
            [{"name": "pkg", "version": "1.0", "hashes": [123]}],
            [{"name": "pkg", "version": "1.0"}],
        ]:
            with pytest.raises((TypeError, ValueError)):
                scan_wheelhouse(lock, malformed, tmp_path, deadline)

    def test_scan_wheelhouse_ignores_hidden_and_dot_prefixed_files(self, tmp_path):
        deadline = 1e12
        wheel_path, digest = _make_wheel(tmp_path, "pkg_alpha", "1.0.0")
        dot_whl = tmp_path / "._pkg_alpha-1.0.0-py3-none-any.whl"
        dot_whl.write_bytes(b"resource fork fake content")
        ds_store = tmp_path / ".DS_Store"
        ds_store.write_bytes(b"junk")

        lock = f"pkg-alpha==1.0.0 --hash={digest}\n".encode()
        packages = parse_locked_requirements(lock)
        wheels, unsupported, mismatched = scan_wheelhouse(
            lock, packages, tmp_path, deadline, diagnose=True
        )
        assert wheel_path.name in wheels
        assert dot_whl.name not in wheels
        assert not any(u["filename"] == dot_whl.name for u in unsupported)
        assert len(unsupported) == 0
        assert len(mismatched) == 0

    def test_diagnose_wheelhouse_success_path(self, tmp_path):
        deadline = 1e12
        _wheel_path, digest = _make_wheel(tmp_path, "lib_core", "0.2.0")
        lock_path = tmp_path / "requirements.lock"
        lock_path.write_bytes(f"lib-core==0.2.0 --hash={digest}\n".encode())

        diagnosis = diagnose_wheelhouse(lock_path, tmp_path, deadline)
        assert diagnosis["all_matched"] is True
        assert diagnosis["matched_packages"] == ["lib-core"]
        assert diagnosis["missing_packages"] == []
        assert diagnosis["unsupported_wheels"] == []
        assert diagnosis["mismatched_wheels"] == []

    def test_diagnose_wheelhouse_missing_and_mismatched(self, tmp_path):
        deadline = 1e12
        wheel_dir = tmp_path / "wheels"
        wheel_dir.mkdir()
        _make_wheel(wheel_dir, "lib_present", "1.0.0")
        bad_wheel = wheel_dir / "lib_rogue-1.0.0-py3-none-any.whl"
        bad_wheel.write_bytes(b"not a valid zip file")

        lock_path = tmp_path / "requirements.lock"
        h1 = "0" * 64
        h2 = "1" * 64
        lock_path.write_bytes(
            (
                f"lib-present==1.0.0 --hash=sha256:{h1}\n"
                f"lib-absent==2.0.0 --hash=sha256:{h2}\n"
            ).encode()
        )

        diagnosis = diagnose_wheelhouse(lock_path, wheel_dir, deadline)
        assert diagnosis["all_matched"] is False
        assert "lib-absent" in diagnosis["missing_packages"]
        assert len(diagnosis["mismatched_wheels"]) + len(diagnosis["unsupported_wheels"]) >= 1

    def test_diagnose_wheelhouse_fail_closed_on_invalid_inputs(self, tmp_path):
        deadline = 1e12
        lock_path = tmp_path / "requirements.lock"
        lock_path.write_bytes(b"valid==1.0 --hash=sha256:" + b"0" * 64)

        for invalid in [None, 123, "/non/existent/path"]:
            with pytest.raises((TypeError, ValueError)):
                diagnose_wheelhouse(invalid, tmp_path, deadline)
            with pytest.raises((TypeError, ValueError)):
                diagnose_wheelhouse(lock_path, invalid, deadline)


class TestImageTagAndDockerSubprocessHardening:
    """SF-IMG-02-A3: Image tag validation hardening and Docker subprocess security."""

    def test_validate_image_tag_rejects_leading_trailing_consecutive_slashes(self):
        for invalid_tag in [
            "/myimage:v1",
            "myimage/:v1",
            "myimage//sub:v1",
            "myimage/sub/:v1",
            "/",
            "//",
            "repo//tag",
            "myimage:v1/",
            "-myimage:v1",
            "myimage:-v1",
            "myimage:",
            "myimage::v1",
        ]:
            with pytest.raises(ValueError):
                validate_image_tag(invalid_tag)

    def test_validate_image_tag_accepts_valid_names(self):
        for valid_tag in [
            "selfforge-agent-sandbox:latest",
            "myorg/myapp:1.0.0",
            "docker.io/myorg/myapp:v1.2-alpha.1",
            "internal-repo.local:5000/team/service:build-123_test",
            "simple-image",
        ]:
            validate_image_tag(valid_tag)

    def test_docker_rejects_empty_arguments(self):
        with pytest.raises(ValueError, match="arguments must not be empty"):
            _docker([], timeout=10)

    def test_docker_rejects_option_injection_in_subcommand(self):
        for leading_option in ["--privileged", "-v", "--help", "-d"]:
            with pytest.raises(ValueError, match="First docker argument must be a subcommand"):
                _docker([leading_option, "image", "list"], timeout=10)

    def test_docker_sanitizes_ansi_escape_sequences_in_output(self):
        raw_error = "Error: \x1b[31;1mred text\x1b[0m failed\r\n"
        fake_run = MagicMock(returncode=1)

        with patch("subprocess.run", return_value=fake_run), patch("tempfile.TemporaryFile") as mock_tmp:
            buffer = io.BytesIO(raw_error.encode("utf-8"))
            mock_tmp.return_value.__enter__.return_value = buffer

            with pytest.raises(ValueError) as excinfo:
                _docker(["image", "inspect", "none"], timeout=10)
            assert "\x1b[" not in str(excinfo.value)
            assert "red text failed" in str(excinfo.value)


class TestDependencyImageResultModelAndIntrospection:
    """SF-IMG-02-A4: DependencyImageResult model, image introspection and build hardening."""

    def test_dependency_image_result_model_validation(self):
        valid_data = {
            "version": 1,
            "tag": "selfforge-test:v1",
            "image_id": "sha256:" + "a" * 64,
            "base_image_id": "sha256:" + "b" * 64,
            "requirements_sha256": "c" * 64,
            "wheels": {"foo-1.0-py3-none-any.whl": "d" * 64},
        }
        model = DependencyImageResult.model_validate(valid_data)
        assert model.version == 1
        assert model.tag == "selfforge-test:v1"
        assert model.image_id == "sha256:" + "a" * 64

        # Rejection of invalid version
        invalid_version = {**valid_data, "version": 2}
        with pytest.raises(ValidationError):
            DependencyImageResult.model_validate(invalid_version)

        # Rejection of invalid image_id format (missing sha256: prefix or uppercase)
        invalid_id = {**valid_data, "image_id": "a" * 64}
        with pytest.raises(ValidationError):
            DependencyImageResult.model_validate(invalid_id)

        # Rejection of invalid requirements_sha256 (not 64 hex)
        invalid_req_hash = {**valid_data, "requirements_sha256": "sha256:" + "c" * 64}
        with pytest.raises(ValidationError):
            DependencyImageResult.model_validate(invalid_req_hash)

    def test_image_exists_and_resolve_image_id(self):
        # Type & boundary validation
        with pytest.raises(TypeError):
            image_exists(123)
        with pytest.raises(ValueError):
            image_exists("")
        with pytest.raises(ValueError):
            image_exists("bad tag;")
        with pytest.raises(ValueError):
            image_exists("valid-image", timeout=-1)

        with pytest.raises(TypeError):
            resolve_image_id(123)
        with pytest.raises(ValueError):
            resolve_image_id("")
        with pytest.raises(ValueError):
            resolve_image_id("bad;image")

        # Mocked behavior
        with patch("selfforge_agent.runtime.images._docker") as mock_dock:
            mock_dock.return_value = "sha256:" + "e" * 64
            assert image_exists("my-image:latest") is True
            resolved = resolve_image_id("my-image:latest")
            assert resolved == "sha256:" + "e" * 64

        with patch("selfforge_agent.runtime.images._docker", side_effect=ValueError("Not found")):
            assert image_exists("non-existent:image") is False
            with pytest.raises(ValueError, match="Failed to resolve image ID"):
                resolve_image_id("non-existent:image")

    def test_build_dependency_image_returns_validated_dict(self, tmp_path):
        wheel_path, digest = _make_wheel(tmp_path, "pkg_sample", "1.0.0")
        lock_path = tmp_path / "requirements.lock"
        lock_path.write_bytes(f"pkg-sample==1.0.0 --hash={digest}\n".encode())

        fake_base_id = "sha256:" + "1" * 64
        fake_built_id = "sha256:" + "2" * 64

        def fake_docker(args, timeout, payload=None):
            if args[0] == "image" and args[1] == "inspect":
                target = args[4]
                if target == fake_built_id:
                    return fake_built_id
                return fake_base_id
            if args[0] == "tag":
                return ""
            if args[0] == "build":
                # Write built id to iidfile
                iidfile_idx = args.index("--iidfile") + 1
                Path(args[iidfile_idx]).write_text(fake_built_id)
                return "Successfully built"
            if args[0] == "image" and args[1] == "rm":
                return ""
            return ""

        with patch("selfforge_agent.runtime.images._docker", side_effect=fake_docker):
            result = build_dependency_image(
                lock_path,
                tmp_path,
                tag="test-output:v1",
                base_image="selfforge-agent-sandbox:latest",
                timeout=60,
            )
            # Verify result adheres to DependencyImageResult
            verified = DependencyImageResult.model_validate(result)
            assert verified.tag == "test-output:v1"
            assert verified.image_id == fake_built_id
            assert verified.base_image_id == fake_base_id
            assert wheel_path.name in verified.wheels


@pytest.mark.parametrize('algorithm', ['Sha256', 'sHA256', 'shA256'])
def test_mixed_case_hash_reaches_profile_and_builder_in_canonical_form(tmp_path, algorithm):
    import hashlib
    import time

    from selfforge_agent.runtime.environment import load_requirement_profile
    from selfforge_agent.runtime.images import _inputs

    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    wheel = b'fixture wheel bytes'
    (wheels / 'fixture-1.0-py3-none-any.whl').write_bytes(wheel)
    digest = hashlib.sha256(wheel).hexdigest()
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text(f'fixture==1.0 --hash={algorithm}:{digest}\n')
    text, _, converted = load_requirement_profile(requirements_file=requirements)
    assert converted
    assert f'--hash=sha256:{digest}' in text
    lock, _ = _inputs(requirements, wheels, time.monotonic() + 10)
    assert f'--hash=sha256:{digest}'.encode() in lock
