"""Explicit offline dependency images from a hash-locked wheelhouse."""

import hashlib
import io
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import uuid4

MAX_LOCK_BYTES = 128 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024 * 1024
_REQUIREMENT = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})+"
)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Dependency image preparation timed out")
    return remaining


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Dependency inputs must be regular files, not symlinks")
    with path.open("rb") as handle:
        contents = handle.read(limit + 1)
    if len(contents) > limit:
        raise ValueError("Dependency input size limit exceeded")
    return contents


def parse_locked_requirements(lock: bytes) -> list[dict]:
    """Parse the same deliberately small lock format for planning and building."""
    if len(lock) > MAX_LOCK_BYTES:
        raise ValueError("Dependency input size limit exceeded")
    lines = lock.decode("utf-8").replace("\r\n", "\n").replace("\\\n", " ").splitlines()
    packages = []
    names = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not _REQUIREMENT.fullmatch(line):
            raise ValueError("Lock requires exact name==version pins with SHA-256 hashes; "
                             "URLs, markers, extras and pip directives are unsupported")
        name, version = line.split()[0].split("==")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in names:
            raise ValueError("Duplicate requirement names are unsupported")
        names.add(canonical)
        packages.append({"name": name, "version": version,
                         "hashes": sorted(set(re.findall(r"sha256:[0-9a-f]{64}", line)))})
    if not packages or len(packages) > 200:
        raise ValueError("Lock must contain 1-200 hashed requirements")
    return packages


def scan_wheelhouse(lock: bytes, packages: list[dict], wheelhouse: Path,
                    deadline: float, *, diagnose: bool = False) -> tuple[dict, list, list]:
    """Share byte/count/deadline limits between builder, plan and diagnostics."""
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ValueError("Wheelhouse must be a directory, not a symlink")
    paths = []
    with os.scandir(wheelhouse) as entries:
        for count, entry in enumerate(entries, 1):
            _remaining(deadline)
            if count > 1000:
                raise ValueError("Wheelhouse directory entry limit exceeded")
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
        if path.is_symlink() or not path.is_file():
            reason = "Dependency inputs must be regular files, not symlinks"
        elif not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", path.name):
            reason = "Unsupported wheel filename"
        elif path.stat().st_size > min(MAX_WHEEL_BYTES, MAX_CONTEXT_BYTES - total):
            reason = "Dependency input size limit exceeded"
        if reason:
            if not diagnose:
                raise ValueError(reason)
            unsupported.append({"filename": path.name, "reason": reason})
            continue
        # A race/read error aborts the scan; it cannot consume bytes and then
        # silently reset the allowance for another file.
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


def read_locked_wheels(lock: bytes, packages: list[dict], wheelhouse: Path | None,
                       deadline: float) -> dict[str, bytes]:
    if wheelhouse is None:
        return {}
    return scan_wheelhouse(lock, packages, wheelhouse, deadline)[0]


def _inputs(requirements: Path, wheelhouse: Path, deadline: float) -> tuple[bytes, dict[str, bytes]]:
    lock = _read(requirements, MAX_LOCK_BYTES)
    packages = parse_locked_requirements(lock)
    wheels = read_locked_wheels(lock, packages, wheelhouse, deadline)
    seen = {"sha256:" + hashlib.sha256(data).hexdigest() for data in wheels.values()}
    if any(not set(pkg["hashes"]).intersection(seen) for pkg in packages):
        raise ValueError("Wheelhouse is missing a hash-matching wheel for a locked requirement")
    return lock, wheels


def validate_image_tag(tag: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9_.-]+)?", tag):
        raise ValueError("Use a local image name with an optional tag")


def _docker(arguments: list[str], timeout: float, payload: bytes | None = None) -> str:
    # Build logs can be large. Keep them off the session/trace and read only a tail.
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(["docker", *arguments], input=payload, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Docker dependency image operation timed out") from exc
        output.seek(0, 2)
        output.seek(max(0, output.tell() - 6_000))
        detail = output.read().decode("utf-8", "replace").strip()
    if result.returncode:
        raise ValueError(f"Docker dependency image operation failed: {detail}")
    return detail


def build_dependency_image(requirements: Path, wheelhouse: Path, *,
                           tag: str, base_image: str = "selfforge-agent-sandbox:latest",
                           timeout: float = 300) -> dict:
    """Build only from explicit wheels; never execute repository build scripts."""
    validate_image_tag(tag)
    if not base_image or base_image.startswith("-") or any(c.isspace() for c in base_image):
        raise ValueError("Invalid base image reference")
    if not 0 < timeout <= 1_800:
        raise ValueError("Image build timeout must be in (0, 1800]")
    deadline = time.monotonic() + timeout
    lock, wheels = _inputs(requirements, wheelhouse, deadline)
    base_id = _docker(["image", "inspect", "--format", "{{.Id}}", base_image],
                      _remaining(deadline))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_id):
        raise ValueError("Docker did not return a local immutable image ID")
    # BuildKit cannot reliably use a bare image ID in FROM. A private alias binds
    # this build to the resolved local image, without changing the operator's tag.
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
        files = {"Dockerfile": dockerfile, "requirements.lock": lock,
                 **{f"wheels/{name}": data for name, data in wheels.items()}}
        for name, data in files.items():
            _remaining(deadline)
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(data))
    try:
        _docker(["tag", base_id, alias], _remaining(deadline))
        with tempfile.TemporaryDirectory(prefix="selfforge-image-") as directory:
            iidfile = Path(directory) / "image.id"
            _docker(["build", "--network=none", "--pull=false", "--tag", tag,
                     "--iidfile", str(iidfile), "-"],
                    _remaining(deadline), buffer.getvalue())
            # Use this build's result, even if another build retags the same name.
            built_id = _read(iidfile, 128).decode("ascii").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", built_id):
                raise ValueError("Docker did not write a valid built image ID")
            image_id = _docker(["image", "inspect", "--format", "{{.Id}}", built_id],
                               _remaining(deadline))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Docker did not return a built image ID")
    finally:
        # Remove only our temporary alias, never the original base or built image.
        try:
            _docker(["image", "rm", alias], 10)
        except (ValueError, OSError):
            pass
    return {"version": 1, "tag": tag, "image_id": image_id, "base_image_id": base_id,
            "requirements_sha256": lock_hash,
            "wheels": {name: hashlib.sha256(data).hexdigest() for name, data in wheels.items()}}
