"""Tests for lockfile conditional markers, PEP 503 name normalization, and safe nonblocking file reads."""
import json
import os
from pathlib import Path

import pytest

from selfforge_agent.runtime.environment import export_hash_locked_requirements
from selfforge_agent.runtime.images import (
    _read,
    parse_locked_requirements,
    scan_wheelhouse,
)
from selfforge_agent.runtime.lockfiles import (
    export_pipfile_requirements,
    export_requirements_txt,
    export_toml_requirements,
)

VALID_HASH = "sha256:" + "a" * 64
VALID_HASH_2 = "sha256:" + "b" * 64


def test_poetry_rejects_platform_conditional_markers(tmp_path: Path) -> None:
    poetry_lock = f"""
[[package]]
name = "demo"
version = "1.0.0"
platform = "win32"
files = [
    {{file = "demo-1.0.0.whl", hash = "{VALID_HASH}"}}
]
"""
    lock_file = tmp_path / "poetry.lock"
    lock_file.write_text(poetry_lock, encoding="utf-8")
    with pytest.raises(ValueError, match="target-aware"):
        export_hash_locked_requirements(lock_file)


def test_pipfile_rejects_platform_and_sys_platform(tmp_path: Path) -> None:
    pipfile_data = {
        "default": {
            "demo": {
                "version": "==1.0.0",
                "platform": "linux",
                "hashes": [VALID_HASH],
            }
        }
    }
    p = tmp_path / "Pipfile.lock"
    p.write_text(json.dumps(pipfile_data), encoding="utf-8")
    with pytest.raises(ValueError, match="target-aware"):
        export_pipfile_requirements(p.read_bytes())

    pipfile_data["default"]["demo"] = {
        "version": "==1.0.0",
        "sys_platform": "darwin",
        "hashes": [VALID_HASH],
    }
    with pytest.raises(ValueError, match="target-aware"):
        export_pipfile_requirements(json.dumps(pipfile_data).encode("utf-8"))

    pipfile_data["default"]["demo"] = {
        "version": "==1.0.0",
        "os_name": "nt",
        "hashes": [VALID_HASH],
    }
    with pytest.raises(ValueError, match="target-aware"):
        export_pipfile_requirements(json.dumps(pipfile_data).encode("utf-8"))


def test_lockfile_rejects_non_string_markers(tmp_path: Path) -> None:
    pipfile_data = {
        "default": {
            "demo": {
                "version": "==1.0.0",
                "platform": 12345,
                "hashes": [VALID_HASH],
            }
        }
    }
    with pytest.raises(ValueError, match="Invalid package marker"):
        export_pipfile_requirements(json.dumps(pipfile_data).encode("utf-8"))

    poetry_lock = f"""
[[package]]
name = "demo"
version = "1.0.0"
platform = ["linux", "darwin"]
files = [
    {{file = "demo-1.0.0.whl", hash = "{VALID_HASH}"}}
]
"""
    with pytest.raises(ValueError, match="Invalid package marker"):
        export_toml_requirements(poetry_lock.encode("utf-8"))


def test_lockfile_pep503_package_name_normalization() -> None:
    poetry_lock = f"""
[[package]]
name = "PyYAML_Core.Lib"
version = "6.0.1"
files = [
    {{file = "demo-6.0.1.whl", hash = "{VALID_HASH}"}}
]
"""
    exported = export_toml_requirements(poetry_lock.encode("utf-8"))
    assert "pyyaml-core-lib==6.0.1" in exported
    assert "PyYAML_Core.Lib" not in exported

    pipfile_data = {
        "default": {
            "Flask_RESTX": {
                "version": "==1.3.0",
                "hashes": [VALID_HASH],
            }
        }
    }
    exported_pip = export_pipfile_requirements(json.dumps(pipfile_data).encode("utf-8"))
    assert "flask-restx==1.3.0" in exported_pip
    assert "Flask_RESTX" not in exported_pip

    flat_reqs = f"Urllib3_Secure.Ext==2.0.0 --hash={VALID_HASH}\n"
    exported_flat = export_requirements_txt(flat_reqs.encode())
    assert "urllib3-secure-ext==2.0.0" in exported_flat

    parsed = parse_locked_requirements(f"Demo_Pkg.Sub==1.0 --hash={VALID_HASH}\n".encode())
    assert parsed[0]["name"] == "demo-pkg-sub"


def test_images_read_rejects_fifos_without_blocking(tmp_path: Path) -> None:
    fifo_path = tmp_path / "test.fifo"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, OSError):
        pytest.skip("mkfifo not supported on this platform")

    # _read on FIFO must not block/hang, and must fail-closed with ValueError
    with pytest.raises(ValueError, match="regular files, not symlinks"):
        _read(fifo_path, 1024)


def test_images_read_rejects_symlinks_and_dirs(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_bytes(b"hello world")

    symlink = tmp_path / "symlink.txt"
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="regular files, not symlinks"):
        _read(symlink, 1024)

    broken_symlink = tmp_path / "broken.txt"
    broken_symlink.symlink_to(tmp_path / "nonexistent.txt")

    with pytest.raises(ValueError, match="regular files, not symlinks"):
        _read(broken_symlink, 1024)

    directory = tmp_path / "subdir"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular files, not symlinks"):
        _read(directory, 1024)


def test_images_read_normal_and_limit(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"0123456789")

    data = _read(target, 10)
    assert data == b"0123456789"

    with pytest.raises(ValueError, match="size limit exceeded"):
        _read(target, 9)


def test_scan_wheelhouse_with_fifo_and_symlink(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()

    real_whl = wheelhouse / "demo-1.0-py3-none-any.whl"
    real_whl.write_bytes(b"content")
    digest = "sha256:" + "d" * 64

    # Symlinked whl inside wheelhouse
    target_whl = tmp_path / "other.whl"
    target_whl.write_bytes(b"other")
    symlink_whl = wheelhouse / "linked-1.0-py3-none-any.whl"
    symlink_whl.symlink_to(target_whl)

    lock = f"demo==1.0 --hash={digest}\nlinked==1.0 --hash={VALID_HASH}\n".encode()
    packages = parse_locked_requirements(lock)

    # In diagnose=True mode, symlinks must be flagged as unsupported
    _, unsupported, _ = scan_wheelhouse(lock, packages, wheelhouse, deadline=1e12, diagnose=True)
    symlink_reasons = [u for u in unsupported if u["filename"] == symlink_whl.name]
    assert len(symlink_reasons) == 1
    assert "regular files" in symlink_reasons[0]["reason"]
