"""Explicit offline dependency images from a hash-locked wheelhouse."""

import hashlib
import io
import math
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

MAX_LOCK_BYTES = 128 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024 * 1024

_REQUIREMENT = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*"
    r"(?:\s+--hash=[sS][hH][aA]256:[0-9a-fA-F]{64})+"
)


class DependencyImageResult(BaseModel):
    """Immutable outcome of an offline dependency image build."""

    version: Literal[1] = 1
    tag: str
    image_id: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")
    base_image_id: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")
    requirements_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    wheels: dict[str, str]


def _finite_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _remaining(deadline: float) -> float:
    if (
        not isinstance(deadline, (int, float))
        or type(deadline) is bool
        or not _finite_number(deadline)
    ):
        raise ValueError("Invalid deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Dependency image preparation timed out")
    return remaining


def _read(path: Path, limit: int) -> bytes:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path instance")
    if not isinstance(limit, int) or type(limit) is bool or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if path.is_symlink() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Dependency inputs must be regular files, not symlinks")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Dependency inputs must be regular files, not symlinks: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("Dependency inputs must be regular files, not symlinks")
        if st.st_size > limit:
            raise ValueError("Dependency input size limit exceeded")
        contents = b""
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            contents += chunk
            remaining -= len(chunk)
        if len(contents) > limit:
            raise ValueError("Dependency input size limit exceeded")
        return contents
    finally:
        os.close(fd)


def parse_locked_requirements(lock: bytes) -> list[dict]:
    """Parse the same deliberately small lock format for planning and building."""
    if not isinstance(lock, bytes):
        raise TypeError("lock must be bytes")
    if len(lock) > MAX_LOCK_BYTES:
        raise ValueError("Dependency input size limit exceeded")
    try:
        decoded = lock.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Invalid UTF-8 encoding in lockfile: {exc}") from exc
    if "\0" in decoded:
        raise ValueError("Lockfile must not contain null bytes")
    lines = decoded.replace("\r\n", "\n").replace("\\\n", " ").splitlines()
    packages = []
    names = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not _REQUIREMENT.fullmatch(line):
            raise ValueError(
                "Lock requires exact name==version pins with SHA-256 hashes; "
                "URLs, markers, extras and pip directives are unsupported"
            )
        name, version = line.split()[0].split("==")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in names:
            raise ValueError("Duplicate requirement names are unsupported")
        names.add(canonical)
        raw_hashes = re.findall(r"(?i:sha256):([0-9a-fA-F]{64})", line)
        normalized_hashes = sorted({f"sha256:{h.lower()}" for h in raw_hashes})
        packages.append({"name": canonical, "version": version, "hashes": normalized_hashes})
    if not packages or len(packages) > 200:
        raise ValueError("Lock must contain 1-200 hashed requirements")
    return packages


def scan_wheelhouse(
    lock: bytes,
    packages: list[dict],
    wheelhouse: Path,
    deadline: float,
    *,
    diagnose: bool = False,
) -> tuple[dict, list, list]:
    """Share byte/count/deadline limits between builder, plan and diagnostics."""
    if not isinstance(lock, bytes):
        raise TypeError("lock must be bytes")
    if not isinstance(packages, list):
        raise TypeError("packages must be a list")
    for pkg in packages:
        if not isinstance(pkg, dict):
            raise TypeError("packages elements must be dictionaries")
        if not isinstance(pkg.get("name"), str) or not isinstance(pkg.get("version"), str):
            raise TypeError("packages elements must have string 'name' and 'version'")
        hashes = pkg.get("hashes")
        if not isinstance(hashes, list) or not all(isinstance(h, str) for h in hashes):
            raise TypeError("packages hashes must be a list of strings")
        if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in hashes):
            raise ValueError("package hash must be lowercase sha256:<64 hex>")
    if not isinstance(wheelhouse, Path):
        raise TypeError("wheelhouse must be a Path instance")
    _remaining(deadline)
    if (
        wheelhouse.is_symlink()
        or any(p.is_symlink() for p in (wheelhouse, *wheelhouse.parents))
        or not wheelhouse.is_dir()
    ):
        raise ValueError("Wheelhouse must be a directory, not a symlink")
    paths = []
    with os.scandir(wheelhouse) as entries:
        for count, entry in enumerate(entries, 1):
            _remaining(deadline)
            if count > 1000:
                raise ValueError("Wheelhouse directory entry limit exceeded")
            if entry.name.startswith("."):
                continue
            if entry.name.endswith(".whl"):
                paths.append(wheelhouse / entry.name)
                if len(paths) > 200:
                    raise ValueError("Wheelhouse file count limit exceeded")
    allowed = {h for pkg in packages for h in pkg["hashes"]}
    wheels, unsupported, mismatched = {}, [], []
    total = len(lock)
    for path in sorted(paths):
        _remaining(deadline)
        reason = None
        if (
            path.is_symlink()
            or any(p.is_symlink() for p in (path, *path.parents))
            or not path.is_file()
        ):
            reason = "Dependency inputs must be regular files, not symlinks"
        elif not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", path.name):
            reason = "Unsupported wheel filename"
        else:
            try:
                st = path.stat()
                if st.st_size == 0:
                    reason = "Wheel file must not be empty"
                elif st.st_size > min(MAX_WHEEL_BYTES, MAX_CONTEXT_BYTES - total):
                    reason = "Dependency input size limit exceeded"
            except OSError as exc:
                reason = f"Cannot stat wheel file: {exc}"
        if reason:
            if not diagnose:
                raise ValueError(reason)
            unsupported.append({"filename": path.name, "reason": reason})
            continue
        data = _read(path, min(MAX_WHEEL_BYTES, MAX_CONTEXT_BYTES - total))
        total += len(data)
        _remaining(deadline)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        _remaining(deadline)
        if digest not in allowed:
            if not diagnose:
                raise ValueError(f"Wheel hash is absent from lock: {path.name}")
            mismatched.append({"filename": path.name, "hash": digest, "size": len(data)})
        wheels[path.name] = data
    _remaining(deadline)
    return wheels, unsupported, mismatched


def read_locked_wheels(
    lock: bytes, packages: list[dict], wheelhouse: Path | None, deadline: float
) -> dict[str, bytes]:
    if wheelhouse is None:
        return {}
    return scan_wheelhouse(lock, packages, wheelhouse, deadline)[0]


def diagnose_wheelhouse(
    requirements_path: Path,
    wheelhouse_path: Path,
    deadline: float,
) -> dict:
    """Shared diagnostic abstraction for validating wheelhouse against locked requirements."""
    if not isinstance(requirements_path, Path):
        raise TypeError("requirements_path must be a Path instance")
    if not isinstance(wheelhouse_path, Path):
        raise TypeError("wheelhouse_path must be a Path instance")
    _remaining(deadline)
    lock = _read(requirements_path, MAX_LOCK_BYTES)
    packages = parse_locked_requirements(lock)
    wheels, unsupported, mismatched = scan_wheelhouse(
        lock, packages, wheelhouse_path, deadline, diagnose=True
    )
    seen_digests = {"sha256:" + hashlib.sha256(data).hexdigest() for data in wheels.values()}
    matched_packages = []
    missing_packages = []
    for pkg in packages:
        pkg_hashes = set(pkg["hashes"])
        if pkg_hashes.intersection(seen_digests):
            matched_packages.append(pkg["name"])
        else:
            missing_packages.append(pkg["name"])
    all_matched = (
        len(missing_packages) == 0
        and len(unsupported) == 0
        and len(mismatched) == 0
    )
    return {
        "all_matched": all_matched,
        "matched_packages": matched_packages,
        "missing_packages": missing_packages,
        "unsupported_wheels": unsupported,
        "mismatched_wheels": mismatched,
    }


def _inputs(
    requirements: Path, wheelhouse: Path, deadline: float
) -> tuple[bytes, dict[str, bytes]]:
    lock = _read(requirements, MAX_LOCK_BYTES)
    packages = parse_locked_requirements(lock)
    wheels = read_locked_wheels(lock, packages, wheelhouse, deadline)
    seen = {"sha256:" + hashlib.sha256(data).hexdigest() for data in wheels.values()}
    if any(not set(pkg["hashes"]).intersection(seen) for pkg in packages):
        raise ValueError("Wheelhouse is missing a hash-matching wheel for a locked requirement")
    return lock, wheels


def validate_image_tag(tag: str) -> None:
    """Validate a local or remote image repository and tag reference."""
    if not isinstance(tag, str):
        raise TypeError("tag must be a string")
    if (
        not tag
        or len(tag) > 255
        or tag.startswith(("-", "/"))
        or tag.endswith(("/", ":"))
        or "//" in tag
        or "::" in tag
        or "\0" in tag
        or any(c.isspace() for c in tag)
    ):
        raise ValueError("Invalid image tag: use a local image name with an optional tag")

    last_colon = tag.rfind(":")
    last_slash = tag.rfind("/")
    if last_colon > last_slash:
        repo = tag[:last_colon]
        tag_part = tag[last_colon + 1 :]
        if not tag_part or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag_part):
            raise ValueError(f"Invalid image tag component: {tag_part}: use a local image name with an optional tag")
    else:
        repo = tag

    if not repo or repo.endswith("/") or repo.startswith("/"):
        raise ValueError("Invalid repository name in image tag: use a local image name with an optional tag")
    parts = repo.split("/")
    for i, part in enumerate(parts):
        if not part or part.startswith("-"):
            raise ValueError("Invalid repository segment in image tag: use a local image name with an optional tag")
        if i == 0 and ":" in part:
            host, port = part.split(":", 1)
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", host) or not re.fullmatch(
                r"[0-9]+", port
            ):
                raise ValueError("Invalid registry domain/port in image tag: use a local image name with an optional tag")
        else:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", part):
                raise ValueError(f"Invalid repository segment: {part}: use a local image name with an optional tag")


def _validate_base_image(base_image: str) -> None:
    if not isinstance(base_image, str):
        raise TypeError("base_image must be a string")
    if (
        not base_image
        or base_image.startswith("-")
        or len(base_image) > 255
        or "\0" in base_image
        or any(c.isspace() for c in base_image)
        or any(c in ";`|&$<>\r\n\\" for c in base_image)
    ):
        raise ValueError("Invalid base image reference")

    if "@" in base_image:
        ref_part, digest_part = base_image.split("@", 1)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_part):
            raise ValueError("Invalid digest in base image reference")
        validate_image_tag(ref_part)
    else:
        validate_image_tag(base_image)


def _docker(arguments: list[str], timeout: float, payload: bytes | None = None) -> str:
    # Build logs can be large. Keep them off the session/trace and read only a tail.
    if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
        raise TypeError("arguments must be a list of strings")
    if not arguments:
        raise ValueError("arguments must not be empty")
    if arguments[0].startswith("-"):
        raise ValueError("First docker argument must be a subcommand, not an option")
    if any("\0" in a for a in arguments):
        raise ValueError("docker arguments must not contain null bytes")
    if (
        not isinstance(timeout, (int, float))
        or type(timeout) is bool
        or not _finite_number(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number")
    if payload is not None and not isinstance(payload, bytes):
        raise TypeError("payload must be bytes or None")

    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                ["docker", *arguments],
                input=payload,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ValueError("Docker is not installed or not in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Docker dependency image operation timed out") from exc
        except OSError as exc:
            raise ValueError(f"Docker execution failed: {exc}") from exc
        output.seek(0, 2)
        output.seek(max(0, output.tell() - 6_000))
        raw_detail = output.read().decode("utf-8", "replace").strip()
        detail = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_detail)
        detail = "".join(
            c for c in detail if c in ("\n", "\t") or (ord(c) >= 32 and ord(c) != 127)
        ).strip()
    if result.returncode:
        raise ValueError(f"Docker dependency image operation failed: {detail}")
    return detail


def image_exists(image_ref: str, timeout: float = 10.0) -> bool:
    """Check if a local Docker image reference or ID exists."""
    if not isinstance(image_ref, str):
        raise TypeError("image_ref must be a string")
    _validate_base_image(image_ref)
    if (
        not isinstance(timeout, (int, float))
        or type(timeout) is bool
        or not _finite_number(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number")
    try:
        res = _docker(["image", "inspect", "--format", "{{.Id}}", image_ref], timeout)
        return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", res.strip()))
    except (ValueError, OSError):
        return False


def resolve_image_id(image_ref: str, timeout: float = 10.0) -> str:
    """Resolve a local Docker image reference to its immutable sha256 ID."""
    if not isinstance(image_ref, str):
        raise TypeError("image_ref must be a string")
    _validate_base_image(image_ref)
    if (
        not isinstance(timeout, (int, float))
        or type(timeout) is bool
        or not _finite_number(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number")
    try:
        image_id = _docker(["image", "inspect", "--format", "{{.Id}}", image_ref], timeout).strip()
    except (ValueError, OSError) as exc:
        raise ValueError(f"Failed to resolve image ID for {image_ref}: {exc}") from exc
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError(f"Docker did not return a valid immutable image ID for {image_ref}")
    return image_id


def build_dependency_image(
    requirements: Path,
    wheelhouse: Path,
    *,
    tag: str,
    base_image: str = "selfforge-agent-sandbox:latest",
    timeout: float = 300,
) -> dict:
    """Build only from explicit wheels; never execute repository build scripts."""
    if not isinstance(requirements, Path):
        raise TypeError("requirements must be a Path instance")
    if not isinstance(wheelhouse, Path):
        raise TypeError("wheelhouse must be a Path instance")
    if (
        not isinstance(timeout, (int, float))
        or type(timeout) is bool
        or not _finite_number(timeout)
        or not 0 < timeout <= 1_800
    ):
        raise ValueError("Image build timeout must be in (0, 1800]")

    validate_image_tag(tag)
    _validate_base_image(base_image)

    deadline = time.monotonic() + timeout
    lock, wheels = _inputs(requirements, wheelhouse, deadline)
    base_id = _docker(
        ["image", "inspect", "--format", "{{.Id}}", base_image],
        _remaining(deadline),
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_id):
        raise ValueError("Docker did not return a local immutable image ID")
    alias = "selfforge-build-base-" + uuid4().hex
    lock_hash = hashlib.sha256(lock).hexdigest()
    dockerfile = (
        f"FROM {alias}\n"
        "COPY requirements.lock /opt/selfforge/requirements.lock\n"
        "COPY wheels/ /opt/selfforge/wheels/\n"
        "RUN python -m pip --isolated install --no-cache-dir --no-index "
        "--find-links=/opt/selfforge/wheels --only-binary=:all: --require-hashes "
        "--force-reinstall -r /opt/selfforge/requirements.lock "
        "&& python -m pip check && python -m pytest --version "
        "&& rm -rf /opt/selfforge/wheels\n"
        "ENV PYTHONPATH=/workspace/src:/workspace\n"
        "WORKDIR /workspace\n"
        f"LABEL org.selfforge.requirements-sha256={lock_hash}\n"
        f"LABEL org.selfforge.base-image-id={base_id}\n"
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        files = {
            "Dockerfile": dockerfile,
            "requirements.lock": lock,
            **{f"wheels/{name}": data for name, data in wheels.items()},
        }
        total_context = sum(len(d) for d in files.values())
        if total_context > MAX_CONTEXT_BYTES:
            raise ValueError("Dependency input size limit exceeded")
        for name, data in files.items():
            _remaining(deadline)
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(data))
    try:
        _docker(["tag", base_id, alias], _remaining(deadline))
        with tempfile.TemporaryDirectory(prefix="selfforge-image-") as directory:
            iidfile = Path(directory) / "image.id"
            _docker(
                [
                    "build",
                    "--network=none",
                    "--pull=false",
                    "--tag",
                    tag,
                    "--iidfile",
                    str(iidfile),
                    "-",
                ],
                _remaining(deadline),
                buffer.getvalue(),
            )
            built_id = _read(iidfile, 128).decode("ascii").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", built_id):
                raise ValueError("Docker did not write a valid built image ID")
            image_id = _docker(
                ["image", "inspect", "--format", "{{.Id}}", built_id],
                _remaining(deadline),
            )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Docker did not return a built image ID")
    finally:
        try:
            _docker(["image", "rm", alias], 10)
        except (ValueError, OSError):
            pass

    result = DependencyImageResult(
        version=1,
        tag=tag,
        image_id=image_id,
        base_image_id=base_id,
        requirements_sha256=lock_hash,
        wheels={name: hashlib.sha256(data).hexdigest() for name, data in wheels.items()},
    )
    return result.model_dump()


__all__ = [
    "MAX_CONTEXT_BYTES",
    "MAX_LOCK_BYTES",
    "MAX_WHEEL_BYTES",
    "DependencyImageResult",
    "build_dependency_image",
    "diagnose_wheelhouse",
    "image_exists",
    "parse_locked_requirements",
    "read_locked_wheels",
    "resolve_image_id",
    "scan_wheelhouse",
    "validate_image_tag",
]
