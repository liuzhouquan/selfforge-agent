"""Finite structured-lock export; no dependency resolution or target selection."""

import re
import tomllib


def _add(requirements: dict, name: object, version: object, hashes: set[str]) -> None:
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", name)
            or not isinstance(version, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,199}", version)):
        raise ValueError("Invalid package name or version in lock file")
    if not hashes:
        raise ValueError("Lock file includes an external package without supported SHA-256 hashes")
    key = re.sub(r"[-_.]+", "-", name).lower()
    if key in requirements:
        if requirements[key][1] != version:
            raise ValueError("Version forks require a target-aware export; cannot flatten this lock")
        requirements[key][2].update(hashes)
    else:
        requirements[key] = (name, version, hashes)


def _hash(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    value = value.removeprefix("sha256:").lower()
    return {"sha256:" + value} if re.fullmatch(r"[0-9a-f]{64}", value) else set()


def _file_hashes(entries: object, label: str) -> set[str]:
    if not isinstance(entries, list):
        raise ValueError(f"Invalid {label} list in lock file")  # noqa: TRY004 - file data
    hashes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, (dict, str)):
            raise ValueError(f"Invalid {label} entry in lock file")  # noqa: TRY004 - file data
        hashes.update(_hash(entry.get("hash") if isinstance(entry, dict) else entry))
    return hashes


def _unconditional(package: dict) -> None:
    for key in ("markers", "marker"):
        if key in package:
            if not isinstance(package[key], str):
                raise ValueError("Invalid package marker in lock file")
            if package[key].strip():
                raise ValueError("Conditional markers require a target-aware export; cannot flatten this lock")


def _render(requirements: dict) -> str:
    if not requirements:
        raise ValueError("No hashed requirements found in lock file")
    lines = sorted(f"{name}=={version}" + "".join(f" --hash={h}" for h in sorted(hashes))
                   for name, version, hashes in requirements.values())
    return "\n".join(lines) + "\n"


def export_toml_requirements(content: bytes) -> str:
    """Export uv or Poetry package pins; skip explicit local/VCS source entries."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Failed to parse lock file as TOML: {exc}") from exc
    packages = data.get("package", [])
    if not isinstance(packages, list) or not packages:
        raise ValueError("Lock file contains no packages")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid metadata in lock file")  # noqa: TRY004 - file data
    metadata_files = metadata.get("files", {})
    if not isinstance(metadata_files, dict):
        raise ValueError("Invalid metadata.files in lock file")  # noqa: TRY004 - file data
    requirements: dict = {}
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("Invalid package entry in lock file")  # noqa: TRY004 - file data
        source = package.get("source", {})
        if not isinstance(source, dict):
            raise ValueError("Invalid package source in lock file")  # noqa: TRY004 - file data
        if (any(key in source for key in ("editable", "virtual", "directory"))
                or source.get("type") in ("git", "directory", "editable", "virtual")):
            continue
        _unconditional(package)
        hashes = _file_hashes(package.get("wheels", []), "wheel")
        sdist = package.get("sdist")
        if sdist is not None:
            if not isinstance(sdist, dict):
                raise ValueError("Invalid sdist in lock file")
            hashes.update(_hash(sdist.get("hash")))
        hashes.update(_file_hashes(package.get("files", []), "files"))
        name = package.get("name")
        if isinstance(name, str) and name in metadata_files:
            hashes.update(_file_hashes(metadata_files[name], "metadata.files"))
        _add(requirements, name, package.get("version"), hashes)
    return _render(requirements)
