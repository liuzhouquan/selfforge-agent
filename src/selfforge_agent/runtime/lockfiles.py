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
        requirements[key] = (key, version, hashes)


def _hash(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    cleaned = value.strip().lower().removeprefix("sha256:")
    return {"sha256:" + cleaned} if re.fullmatch(r"[0-9a-f]{64}", cleaned) else set()


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
    for key in ("markers", "marker", "resolution-markers", "platform", "sys_platform", "os_name"):
        if key in package:
            val = package[key]
            if not isinstance(val, str):
                raise ValueError("Invalid package marker in lock file")
            cleaned = val.strip()
            if cleaned and cleaned != "*":
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
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError) as exc:
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


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys in lock file")
        result[key] = value
    return result


def export_pipfile_requirements(content: bytes) -> str:
    """Export unconditional exact pins from default and develop, rejecting malformed sections."""
    import json

    try:
        data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("Failed to parse lock file as JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Lock file contains no packages")  # noqa: TRY004 - file data
    if "_meta" in data and not isinstance(data["_meta"], dict):
        raise ValueError("Invalid Pipfile metadata in lock file")
    for name in ("default", "develop"):
        if name in data and not isinstance(data[name], dict):
            raise ValueError("Invalid Pipfile section in lock file")
    if not data.get("default") and not data.get("develop"):
        raise ValueError("Lock file contains no packages")
    requirements: dict = {}
    for section in ("default", "develop"):
        for name, package in data.get(section, {}).items():
            if not isinstance(package, dict):
                raise ValueError("Invalid package entry in lock file")  # noqa: TRY004 - file data
            if package.get("editable") is True or any(k in package for k in ("git", "file", "path", "ref")):
                continue
            _unconditional(package)
            version = package.get("version")
            if not isinstance(version, str) or not version.startswith("=="):
                raise ValueError("Invalid exact package version in lock file")
            raw_hashes = package.get("hashes", [])
            if not isinstance(raw_hashes, list) or any(not isinstance(h, str) for h in raw_hashes):
                raise ValueError("Invalid hash list in lock file")
            hashes = set().union(*(_hash(h) for h in raw_hashes)) if raw_hashes else set()
            _add(requirements, name, version[2:], hashes)
    return _render(requirements)


def export_requirements_txt(content: bytes) -> str:
    """Export hash-locked requirements from pip-compile or flat requirements.txt format."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Failed to parse lock file as text") from exc

    joined = text.replace("\r\n", "\n").replace("\\\n", " ")
    requirements: dict = {}

    for raw_line in joined.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-") and not line.startswith("--hash"):
            raise ValueError("URLs, markers, extras and pip directives are unsupported")
        if ";" in line:
            raise ValueError("Conditional markers require a target-aware export; cannot flatten this lock")
        if "[" in line or "]" in line:
            raise ValueError("Extras require a target-aware export; cannot flatten this lock")
        parts = line.split()
        pkg_spec = parts[0]
        if "==" not in pkg_spec:
            raise ValueError("Lock requires exact name==version pins with SHA-256 hashes")
        name, version = pkg_spec.split("==", 1)
        if not name or not version or any(op in version for op in ("<", ">", "~", "=")):
            raise ValueError("Invalid exact package version in lock file")

        hashes: set[str] = set()
        for token in parts[1:]:
            if token.startswith("--hash="):
                h_val = token.split("=", 1)[1]
                parsed_hash = _hash(h_val)
                if not parsed_hash:
                    raise ValueError("Invalid SHA-256 hash in requirements lock")
                hashes.update(parsed_hash)
            else:
                raise ValueError("URLs, markers, extras and pip directives are unsupported")

        _add(requirements, name, version, hashes)

    return _render(requirements)
