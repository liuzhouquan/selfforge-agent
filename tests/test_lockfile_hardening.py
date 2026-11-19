"""Comprehensive security, depth, and normalization hardening tests for lockfiles (SF-LOCK-01)."""

from __future__ import annotations

import pytest

from selfforge_agent.runtime.lockfiles import (
    RequirementCollector,
    export_pipfile_requirements,
    export_requirements_txt,
    export_toml_requirements,
)

VALID_HASH_1 = "sha256:" + "a" * 64
VALID_HASH_2 = "sha256:" + "b" * 64
VALID_HASH_UPPER = "sha256:" + ("C" * 64)


class TestLockfileEntryDefense:
    def test_export_rejects_non_bytes_content(self) -> None:
        for bad_content in ("string_content", None, 12345, ["list"]):
            with pytest.raises(TypeError, match="(?i)bytes"):
                export_toml_requirements(bad_content)  # type: ignore[arg-type]
            with pytest.raises(TypeError, match="(?i)bytes"):
                export_pipfile_requirements(bad_content)  # type: ignore[arg-type]
            with pytest.raises(TypeError, match="(?i)bytes"):
                export_requirements_txt(bad_content)  # type: ignore[arg-type]

    def test_export_rejects_empty_content(self) -> None:
        for empty_b in (b"", b"   \n\r\t   "):
            with pytest.raises(ValueError, match="(?i)empty"):
                export_toml_requirements(empty_b)
            with pytest.raises(ValueError, match="(?i)empty"):
                export_pipfile_requirements(empty_b)
            with pytest.raises(ValueError, match="(?i)empty"):
                export_requirements_txt(empty_b)

    def test_export_rejects_oversized_content(self) -> None:
        oversized = b"a" * (5 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="(?i)size limit"):
            export_toml_requirements(oversized)
        with pytest.raises(ValueError, match="(?i)size limit"):
            export_pipfile_requirements(oversized)
        with pytest.raises(ValueError, match="(?i)size limit"):
            export_requirements_txt(oversized)

    def test_pipfile_rejects_excessive_json_nesting_depth(self) -> None:
        # Build deeply nested dictionary > 50 levels
        inner: dict = {"version": "==1.0.0", "hashes": [VALID_HASH_1]}
        for _ in range(55):
            inner = {"sub": inner}
        import json
        deep_data = json.dumps({"default": {"deep_pkg": inner}}).encode("utf-8")
        with pytest.raises(ValueError, match="(?i)nesting depth"):
            export_pipfile_requirements(deep_data)

    def test_export_rejects_package_flood_dos(self) -> None:
        # Construct TOML with 5001 packages
        lines = []
        for i in range(5001):
            lines.append(f'[[package]]\nname = "pkg{i}"\nversion = "1.0"\nfiles = [{{hash = "{VALID_HASH_1}"}}]')
        toml_flood = "\n".join(lines).encode("utf-8")
        with pytest.raises(ValueError, match="(?i)package count"):
            export_toml_requirements(toml_flood)

    def test_export_rejects_excessive_files_per_package(self) -> None:
        files = [f'{{hash = "{VALID_HASH_1}"}}' for _ in range(501)]
        toml_files_flood = (
            '[[package]]\nname = "heavy"\nversion = "1.0"\nfiles = [' + ",".join(files) + "]"
        ).encode("utf-8")
        with pytest.raises(ValueError, match="(?i)file entries"):
            export_toml_requirements(toml_files_flood)


class TestLockfileMarkerAndConditionsHardening:
    def test_poetry_rejects_extended_pep508_markers(self) -> None:
        extended_markers = [
            'platform_machine = "x86_64"',
            'platform_system = "Linux"',
            'implementation_name = "cpython"',
            'extra = "dev"',
            'environment = "production"',
        ]
        for marker_spec in extended_markers:
            content = f"""
[[package]]
name = "demo"
version = "1.0.0"
{marker_spec}
files = [
    {{hash = "{VALID_HASH_1}"}}
]
""".encode()
            with pytest.raises(ValueError, match="target-aware"):
                export_toml_requirements(content)

    def test_poetry_rejects_optional_packages(self) -> None:
        content = f"""
[[package]]
name = "optional-pkg"
version = "1.0.0"
optional = true
files = [
    {{hash = "{VALID_HASH_1}"}}
]
""".encode()
        with pytest.raises(ValueError, match="(?i)optional"):
            export_toml_requirements(content)

    def test_pipfile_rejects_extended_markers_and_targets(self) -> None:
        import json
        bad_packages = [
            {"version": "==1.0.0", "hashes": [VALID_HASH_1], "platform_machine": "arm64"},
            {"version": "==1.0.0", "hashes": [VALID_HASH_1], "targets": ["win32"]},
            {"version": "==1.0.0", "hashes": [VALID_HASH_1], "extra": "docs"},
            {"version": "==1.0.0", "hashes": [VALID_HASH_1], "environment": "test"},
        ]
        for pkg_meta in bad_packages:
            data = json.dumps({"default": {"demo": pkg_meta}}).encode()
            with pytest.raises(ValueError, match="(?i)(target-aware|invalid package marker)"):
                export_pipfile_requirements(data)

    def test_requirements_txt_rejects_pip_options_and_extras(self) -> None:
        disallowed_lines = [
            "-r other_requirements.txt",
            "-i https://pypi.org/simple",
            "--extra-index-url https://custom.pypi.org",
            "-f /path/to/wheels",
            "-c constraints.txt",
            f"requests[security]==2.28.1 --hash={VALID_HASH_1}",
            f"demo==1.0.0; sys_platform == 'linux' --hash={VALID_HASH_1}",
        ]
        for bad_line in disallowed_lines:
            with pytest.raises(ValueError, match="(?i)(unsupported|target-aware)"):
                export_requirements_txt(bad_line.encode())


class TestLockfileHashAndNormalizationHardening:
    def test_hash_handles_sha256_equals_prefix_and_uppercase(self) -> None:
        # pip-compile sometimes emits --hash=sha256=... instead of --hash=sha256:...
        req_txt = f"demo==1.0.0 --hash=sha256={VALID_HASH_UPPER.removeprefix('sha256:')}\n".encode()
        result = export_requirements_txt(req_txt)
        assert "demo==1.0.0" in result
        assert f"sha256:{('c' * 64)}" in result

    def test_hash_rejects_non_sha256_algorithms(self) -> None:
        content = b"""
[[package]]
name = "md5-pkg"
version = "1.0.0"
files = [
    {hash = "md5:1234567890abcdef1234567890abcdef"}
]
"""
        with pytest.raises(ValueError, match="(?i)supported SHA-256 hashes"):
            export_toml_requirements(content)

    def test_pep503_name_normalization_and_deduplication(self) -> None:
        content = f"""
[[package]]
name = "Foo.Bar_Baz"
version = "1.0.0"
files = [
    {{hash = "{VALID_HASH_1}"}}
]

[[package]]
name = "foo-bar-baz"
version = "1.0.0"
files = [
    {{hash = "{VALID_HASH_2}"}}
]
""".encode()
        result = export_toml_requirements(content)
        lines = result.strip().splitlines()
        # Must be merged into a single normalized entry with both hashes
        assert len(lines) == 1
        assert lines[0].startswith("foo-bar-baz==1.0.0")
        assert f"--hash={VALID_HASH_1}" in lines[0]
        assert f"--hash={VALID_HASH_2}" in lines[0]

    def test_version_fork_conflict_detected_across_sections(self) -> None:
        import json
        pipfile_data = {
            "default": {
                "demo": {"version": "==1.0.0", "hashes": [VALID_HASH_1]}
            },
            "develop": {
                "demo": {"version": "==2.0.0", "hashes": [VALID_HASH_2]}
            },
        }
        with pytest.raises(ValueError, match="Version forks require a target-aware export"):
            export_pipfile_requirements(json.dumps(pipfile_data).encode("utf-8"))


class TestRequirementCollectorSharedAbstraction:
    def test_collector_validates_and_normalizes_entries(self) -> None:
        collector = RequirementCollector()
        collector.add("Flask_Cors", "3.0.10", [VALID_HASH_1])
        collector.add("flask-cors", "3.0.10", [VALID_HASH_UPPER])

        rendered = collector.render()
        assert "flask-cors==3.0.10" in rendered
        assert f"--hash={VALID_HASH_1}" in rendered
        assert f"--hash={('sha256:' + 'c' * 64)}" in rendered

    def test_collector_rejects_version_forks_and_empty_hashes(self) -> None:
        collector = RequirementCollector()
        with pytest.raises(ValueError, match="without supported SHA-256"):
            collector.add("demo", "1.0.0", [])

        collector.add("demo", "1.0.0", [VALID_HASH_1])
        with pytest.raises(ValueError, match="Version forks"):
            collector.add("demo", "2.0.0", [VALID_HASH_2])

    def test_collector_rejects_empty_render(self) -> None:
        collector = RequirementCollector()
        with pytest.raises(ValueError, match="No hashed requirements"):
            collector.render()
