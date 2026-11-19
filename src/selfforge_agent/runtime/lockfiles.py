"""Finite structured-lock export; no dependency resolution or target selection."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable

MAX_LOCK_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_LOCK_PACKAGES = 5000
MAX_PACKAGE_FILES = 500
MAX_JSON_DEPTH = 50

_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_PKG_VER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,199}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

MARKER_KEYS = (
    "markers",
    "marker",
    "resolution-markers",
    "platform",
    "sys_platform",
    "os_name",
    "platform_machine",
    "platform_system",
    "platform_release",
    "platform_version",
    "implementation_name",
    "implementation_version",
    "extra",
    "environment",
    "targets",
)


def _validate_content_bytes(content: object) -> bytes:
    if not isinstance(content, bytes):
        raise TypeError(f"Lock file content must be bytes, got {type(content).__name__}")
    if len(content) > MAX_LOCK_BYTES:
        raise ValueError(f"Lock file content exceeds 5 MiB size limit ({len(content)} bytes)")
    if not content.strip():
        raise ValueError("Lock file is empty")
    return content


def normalize_package_name(name: object) -> str:
    if not isinstance(name, str) or not _PKG_NAME_RE.fullmatch(name.strip()) or "\x00" in name:
        raise ValueError("Invalid package name in lock file")
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def validate_package_version(version: object) -> str:
    if not isinstance(version, str) or not _PKG_VER_RE.fullmatch(version.strip()) or "\x00" in version:
        raise ValueError("Invalid package version in lock file")
    clean = version.strip()
    if any(op in clean for op in ("<", ">", "~", "=")):
        raise ValueError("Invalid exact package version in lock file")
    return clean


def _hash(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    cleaned = value.strip().lower()
    if cleaned.startswith(("sha256:", "sha256=")):
        cleaned = cleaned[7:]
    if "\x00" in cleaned:
        return set()
    return {"sha256:" + cleaned} if _HEX_64_RE.fullmatch(cleaned) else set()


def _file_hashes(entries: object, label: str) -> set[str]:
    if not isinstance(entries, list):
        raise ValueError(f"Invalid {label} list in lock file")  # noqa: TRY004 - file data
    if len(entries) > MAX_PACKAGE_FILES:
        raise ValueError(f"Package exceeds maximum file entries of {MAX_PACKAGE_FILES}")
    hashes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, (dict, str)):
            raise ValueError(f"Invalid {label} entry in lock file")  # noqa: TRY004 - file data
        hashes.update(_hash(entry.get("hash") if isinstance(entry, dict) else entry))
    return hashes


def _unconditional(package: dict) -> None:
    if package.get("optional") is True:
        raise ValueError("Optional packages require a target-aware export; cannot flatten this lock")
    for key in MARKER_KEYS:
        if key in package:
            val = package[key]
            if val is None:
                continue
            if not isinstance(val, str):
                raise ValueError("Invalid package marker in lock file")
            cleaned = val.strip()
            if cleaned and cleaned != "*":
                raise ValueError("Conditional markers require a target-aware export; cannot flatten this lock")


def _check_depth(obj: object, max_depth: int = MAX_JSON_DEPTH) -> None:
    stack = [(obj, 1)]
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"Lock file exceeds maximum JSON nesting depth ({max_depth})")
        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))


class RequirementCollector:
    """Bounded, PEP-503 normalizing accumulator for hash-locked requirements with version-fork conflict detection."""

    def __init__(self, max_packages: int = MAX_LOCK_PACKAGES) -> None:
        self.max_packages = max_packages
        self._requirements: dict[str, tuple[str, str, set[str]]] = {}

    def add(self, name: object, version: object, hashes: set[str] | Iterable[str]) -> None:
        key = normalize_package_name(name)
        clean_version = validate_package_version(version)
        normalized_hashes = set()
        for h in hashes:
            normalized_hashes.update(_hash(h))
        if not normalized_hashes:
            raise ValueError("Lock file includes an external package without supported SHA-256 hashes")

        if key in self._requirements:
            _existing_name, existing_version, existing_hashes = self._requirements[key]
            if existing_version != clean_version:
                raise ValueError("Version forks require a target-aware export; cannot flatten this lock")
            existing_hashes.update(normalized_hashes)
        else:
            if len(self._requirements) >= self.max_packages:
                raise ValueError(f"Lock file exceeds maximum package count of {self.max_packages}")
            self._requirements[key] = (key, clean_version, set(normalized_hashes))

    def render(self) -> str:
        if not self._requirements:
            raise ValueError("No hashed requirements found in lock file")
        lines = sorted(
            f"{name}=={version}" + "".join(f" --hash={h}" for h in sorted(hashes))
            for name, version, hashes in self._requirements.values()
        )
        return "\n".join(lines) + "\n"


# Keep _add and _render for backward compatibility
def _add(requirements: dict, name: object, version: object, hashes: set[str]) -> None:
    key = normalize_package_name(name)
    clean_version = validate_package_version(version)
    normalized_hashes = set()
    for h in hashes:
        normalized_hashes.update(_hash(h))
    if not normalized_hashes:
        raise ValueError("Lock file includes an external package without supported SHA-256 hashes")
    if key in requirements:
        if requirements[key][1] != clean_version:
            raise ValueError("Version forks require a target-aware export; cannot flatten this lock")
        requirements[key][2].update(normalized_hashes)
    else:
        requirements[key] = (key, clean_version, set(normalized_hashes))


def _render(requirements: dict) -> str:
    collector = RequirementCollector()
    for name, version, hashes in requirements.values():
        collector.add(name, version, hashes)
    return collector.render()


def export_toml_requirements(content: bytes) -> str:
    """Export uv or Poetry package pins; skip explicit local/VCS source entries."""
    _validate_content_bytes(content)
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError(f"Failed to parse lock file as TOML: {exc}") from exc
    packages = data.get("package", [])
    if not isinstance(packages, list) or not packages:
        raise ValueError("Lock file contains no packages")
    if len(packages) > MAX_LOCK_PACKAGES:
        raise ValueError(f"Lock file exceeds maximum package count of {MAX_LOCK_PACKAGES}")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid metadata in lock file")  # noqa: TRY004 - file data
    metadata_files = metadata.get("files", {})
    if not isinstance(metadata_files, dict):
        raise ValueError("Invalid metadata.files in lock file")  # noqa: TRY004 - file data
    collector = RequirementCollector()
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
        collector.add(name, package.get("version"), hashes)
    return collector.render()


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys in lock file")
        result[key] = value
    return result


def export_pipfile_requirements(content: bytes) -> str:
    """Export unconditional exact pins from default and develop, rejecting malformed sections."""
    _validate_content_bytes(content)
    try:
        data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("Failed to parse lock file as JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Lock file contains no packages")  # noqa: TRY004 - file data
    _check_depth(data)
    if "_meta" in data and not isinstance(data["_meta"], dict):
        raise ValueError("Invalid Pipfile metadata in lock file")
    for name in ("default", "develop"):
        if name in data and not isinstance(data[name], dict):
            raise ValueError("Invalid Pipfile section in lock file")
    if not data.get("default") and not data.get("develop"):
        raise ValueError("Lock file contains no packages")

    total_pkgs = len(data.get("default", {})) + len(data.get("develop", {}))
    if total_pkgs > MAX_LOCK_PACKAGES:
        raise ValueError(f"Lock file exceeds maximum package count of {MAX_LOCK_PACKAGES}")

    collector = RequirementCollector()
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
            clean_ver = version[2:].strip()
            collector.add(name, clean_ver, hashes)
    return collector.render()


def export_requirements_txt(content: bytes) -> str:
    """Export hash-locked requirements from pip-compile or flat requirements.txt format."""
    _validate_content_bytes(content)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Failed to parse lock file as text") from exc

    joined = text.replace("\r\n", "\n").replace("\\\n", " ")
    collector = RequirementCollector()

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

        collector.add(name, version, hashes)

    return collector.render()
