"""Bind Coding sessions to one execution mode and immutable Docker image, and detect repository environment."""

import hashlib
import math
import re
import shlex
import time
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from selfforge_agent.runtime.execution import (
    DockerExecutor,
    Executor,
    HostExecutor,
    resolve_docker_image,
)
from selfforge_agent.runtime.images import (
    MAX_LOCK_BYTES,
    _read,
    _remaining,
    parse_locked_requirements,
    read_locked_wheels,
    scan_wheelhouse,
    validate_image_tag,
)
from selfforge_agent.runtime.lockfiles import (
    export_pipfile_requirements,
    export_requirements_txt,
    export_toml_requirements,
)
from selfforge_agent.runtime.session import SessionStore


class RepositoryEnvironment(BaseModel):
    repository: str
    layout: str  # "src" or "flat"
    dependency_files: list[str] = Field(default_factory=list)
    lock_files: list[str] = Field(default_factory=list)
    test_framework: str = "pytest"
    test_paths: list[str] = Field(default_factory=list)
    has_hash_lock: bool = False
    python_requires: str | None = None
    suggested_image: str | None = None
    warnings: list[str] = Field(default_factory=list)


def detect_repository_environment(repository: Path) -> dict:
    """Inspect repository structure and detect dependency files, layout, and test setup."""
    repo = repository.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError("Repository directory does not exist")

    # Detect layout
    src_dir = repo / "src"
    layout = "src" if src_dir.is_dir() and any(src_dir.iterdir()) else "flat"

    # Known dependency and lock files
    known_dep_files = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "requirements-dev.txt", "test-requirements.txt", "Pipfile",
    ]
    known_lock_files = [
        "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock",
    ]

    dep_files = [name for name in known_dep_files if (repo / name).is_file()]
    lock_files = [name for name in known_lock_files if (repo / name).is_file()]
    for extra in repo.glob("*lock*.txt"):
        if extra.is_file() and extra.name not in lock_files:
            lock_files.append(extra.name)

    # Check for SHA-256 hash-locks
    has_hash_lock = False
    for filename in lock_files + dep_files:
        filepath = repo / filename
        if filepath.is_file() and not filepath.is_symlink() and filepath.stat().st_size <= 5 * 1024 * 1024:
            try:
                content = _read(filepath, 5 * 1024 * 1024).decode("utf-8", errors="ignore")
                if ("--hash=sha256:" in content
                        or bool(re.search(r'(?:hash\s*=\s*["\'](?:sha256:)?[0-9a-fA-F]{64}["\']|["\']sha256:[0-9a-fA-F]{64}["\'])', content))):
                    has_hash_lock = True
                    break
            except (OSError, ValueError):
                pass

    # Extract python_requires from pyproject.toml if present
    python_requires = None
    pyproject_path = repo / "pyproject.toml"
    if pyproject_path.is_file() and not pyproject_path.is_symlink() and pyproject_path.stat().st_size <= 256 * 1024:
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            project = data.get("project", {})
            if isinstance(project, dict) and isinstance(project.get("requires-python"), str):
                python_requires = project["requires-python"]
        except (tomllib.TOMLDecodeError, OSError):
            pass

    # Detect test paths
    test_paths = []
    for candidate in ("tests", "test"):
        if (repo / candidate).is_dir():
            test_paths.append(candidate)

    # Warnings
    warnings = []
    if not has_hash_lock:
        warnings.append(
            "No hash-locked requirements found; offline sandbox build requires exact pins with --require-hashes."
        )

    # Suggested profile image if a known profile exists
    suggested_image = None
    app_root = Path(__file__).resolve().parents[3]
    profile_path = app_root / "docker" / "profiles" / f"{repo.name}.lock"
    if profile_path.is_file():
        suggested_image = f"selfforge-{repo.name}:locked"

    env = RepositoryEnvironment(
        repository=str(repo),
        layout=layout,
        dependency_files=sorted(dep_files),
        lock_files=sorted(lock_files),
        test_framework="pytest",
        test_paths=test_paths,
        has_hash_lock=has_hash_lock,
        python_requires=python_requires,
        suggested_image=suggested_image,
        warnings=warnings,
    )
    return env.model_dump()


def bind_environment(executor: Executor | None, session: SessionStore, *,
                     timeout: float, resume: bool) -> None:
    if not isinstance(executor, (DockerExecutor, HostExecutor)):
        return
    saved = session.environment()
    sandbox = "docker" if isinstance(executor, DockerExecutor) else "host"
    if saved is not None and saved["sandbox"] != sandbox:
        raise ValueError("Session execution mode differs; select the original mode "
                         "(Host still requires explicit trust) or start a new session")
    image_id = ""
    if isinstance(executor, DockerExecutor):
        reference = (saved["image_id"] if saved is not None and not executor.image_explicit
                     else executor.image)
        image_id = resolve_docker_image(reference, timeout)
        if saved is not None and image_id != saved["image_id"]:
            raise ValueError("Session Docker image differs; omit --image to reuse the saved "
                             "image, or start a new session for a different environment")
        executor.image = image_id
    if saved is None:
        # Old test evidence predates environment binding. Persist invalidation
        # first so an interrupted migration cannot preserve unknown evidence.
        if resume:
            session.record_verification(False)
        session.record_environment({"version": 1, "sandbox": sandbox, "image_id": image_id})


def export_hash_locked_requirements(lock_path: Path, output_path: Path | None = None) -> str:
    """Extract hash-locked requirement lines (name==version --hash=sha256:...) from uv.lock, poetry.lock, Pipfile.lock, or requirements.lock."""
    if not lock_path.is_file():
        raise ValueError(f"Lock file does not exist: {lock_path}")
    if lock_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Lock file exceeds 5 MiB size limit")

    content = _read(lock_path, 5 * 1024 * 1024)
    is_json = (lock_path.name == "Pipfile.lock" or lock_path.suffix == ".json"
               or content.lstrip().startswith(b"{"))
    is_req = (lock_path.name in ("requirements.lock", "requirements.txt")
              or lock_path.suffix == ".txt"
              or ("--hash=" in content.decode("utf-8", errors="ignore") and b"[[package]]" not in content))
    if content.lstrip().startswith(b"{") or (is_json and not is_req):
        result = export_pipfile_requirements(content)
    elif is_req:
        result = export_requirements_txt(content)
    else:
        result = export_toml_requirements(content)
    if output_path is not None:
        output_path.write_text(result, encoding="utf-8")
    return result


def load_requirement_profile(repository: Path | None = None, *, lock_file: Path | None = None,
                             requirements_file: Path | None = None) -> tuple[str, Path, bool]:
    """Use one bounded profile loader for planning, auditing and explicit exports."""
    forced_flat = requirements_file is not None
    path = requirements_file or lock_file
    if path is None:
        if repository is None:
            raise ValueError("Either repo, lock_file, or requirements_file must be specified")
        path = next((repository / n for n in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock")
                     if (repository / n).is_file()), None)
        if path is None:
            raise ValueError("No supported lockfile (uv.lock, poetry.lock, Pipfile.lock, requirements.lock) found in repository")
    path = path.expanduser().absolute()
    contents = _read(path, MAX_LOCK_BYTES if forced_flat else 5 * 1024 * 1024)
    structured = not forced_flat and (path.name in {"uv.lock", "poetry.lock", "Pipfile.lock"}
                  or path.suffix in {".toml", ".json"} or b"[[package]]" in contents
                  or contents.lstrip().startswith(b"{"))
    if structured:
        return export_hash_locked_requirements(path), path, True
    # An already buildable file can be used directly. Other supported flat files
    # need explicit normalization before pip/build consumes their source path.
    contents = _read(path, MAX_LOCK_BYTES)
    try:
        parse_locked_requirements(contents)
        return contents.decode("utf-8"), path, False
    except ValueError:
        return export_requirements_txt(contents), path, True


def plan_environment_preparation(
    repository: Path,
    *,
    wheelhouse: Path | None = None,
    tag: str | None = None,
    lock_file: Path | None = None,
    requirements_file: Path | None = None,
) -> dict:
    """Generate an offline dependency preparation plan and audit wheelhouse completeness."""
    repo = repository.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repository}")

    detected = detect_repository_environment(repo)
    layout = detected["layout"]

    req_text, req_source_path, export_needed = load_requirement_profile(
        repo, lock_file=lock_file, requirements_file=requirements_file)

    lock_bytes = req_text.encode("utf-8")
    packages = parse_locked_requirements(lock_bytes)
    req_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    target_tag = tag or "selfforge-dependencies:locked"
    validate_image_tag(target_tag)
    wheelhouse_path = wheelhouse.expanduser().absolute() if wheelhouse else None
    wheels = read_locked_wheels(lock_bytes, packages, wheelhouse_path, time.monotonic() + 10)
    existing_wheels = {"sha256:" + hashlib.sha256(data).hexdigest(): name
                       for name, data in wheels.items()}

    satisfied = []
    missing = []
    for pkg in packages:
        hashes = set(pkg["hashes"])
        matching_file = next((existing_wheels[h] for h in hashes if h in existing_wheels), None)
        if matching_file:
            satisfied.append({
                "name": pkg["name"],
                "version": pkg["version"],
                "wheel_file": matching_file,
            })
        else:
            missing.append({
                "name": pkg["name"],
                "version": pkg["version"],
                "required_hashes": sorted(hashes),
            })

    ready = len(missing) == 0 and len(packages) > 0
    whl_dest = str(wheelhouse_path) if wheelhouse_path else str(repo / "wheelhouse")
    req_arg = str(req_source_path.with_name("selfforge-requirements.lock")
                  if export_needed else req_source_path)
    export_command = (shlex.join(["selfforge-agent", "sandbox", "export-lock", "--lock",
                                  str(req_source_path), "--output", req_arg])
                      if export_needed else None)
    download_cmd = shlex.join([
        "python", "-m", "pip", "download", "--isolated", "--index-url", "https://pypi.org/simple",
        "--only-binary=:all:", "--require-hashes", "--dest", whl_dest, "-r", req_arg,
    ])
    build_cmd = shlex.join([
        "selfforge-agent", "sandbox", "build", "--requirements", req_arg,
        "--wheelhouse", whl_dest, "--tag", target_tag,
    ])

    return {
        "version": 1,
        "repository": str(repo),
        "layout": layout,
        "tag": target_tag,
        "ready_to_build": ready,
        "status": "ready" if ready else "pending_wheels",
        "requirements_sha256": req_sha256,
        "package_count": len(packages),
        "packages": packages,
        "wheelhouse": {
            "path": str(wheelhouse_path) if wheelhouse_path else None,
            "total_wheels": len(wheels),
            "satisfied_count": len(satisfied),
            "missing_count": len(missing),
            "satisfied_packages": satisfied,
            "missing_packages": missing,
        },
        "export_command": export_command,
        "requirements_content": req_text,
        "readiness_scope": "Hash-allowed inputs only; wheel format, platform and dependency resolution require build",
        "download_command": download_cmd,
        "build_command": build_cmd,
        "safety_invariants": [
            "Offline build only (--network=none)",
            "Hash-locked wheels required (--require-hashes)",
            "No arbitrary repository build scripts or hooks executed",
        ],
    }


def audit_wheelhouse(
    repo: Path | None = None,
    *,
    wheelhouse: Path,
    lock_file: Path | None = None,
    requirements_file: Path | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Audit a wheelhouse against locked requirements and report readiness and discrepancies."""
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("Audit timeout must be finite and in (0, 300]")
    deadline = time.monotonic() + timeout
    wheelhouse = wheelhouse.expanduser().absolute()
    if not wheelhouse.exists():
        raise ValueError(f"Wheelhouse directory does not exist: {wheelhouse}")
    req_text, req_source_path, _ = load_requirement_profile(
        repo.expanduser().absolute() if repo is not None else None,
        lock_file=lock_file, requirements_file=requirements_file)
    lock_bytes = req_text.encode("utf-8")
    packages = parse_locked_requirements(lock_bytes)
    _remaining(deadline)
    wheels, unsupported_wheels, mismatched_wheels = scan_wheelhouse(
        lock_bytes, packages, wheelhouse, deadline, diagnose=True)
    records = [{"filename": name, "hash": "sha256:" + hashlib.sha256(data).hexdigest()}
               for name, data in wheels.items()]
    matched_packages, missing_packages = [], []
    for pkg in packages:
        matches = [r for r in records if r["hash"] in pkg["hashes"]]
        if matches:
            matched_packages.append({"name": pkg["name"], "version": pkg["version"],
                                     "matched_wheels": [m["filename"] for m in matches],
                                     "matched_hashes": [m["hash"] for m in matches]})
        else:
            missing_packages.append({"name": pkg["name"], "version": pkg["version"], "hashes": pkg["hashes"]})
    total_bytes = sum(len(data) for data in wheels.values())
    _remaining(deadline)

    ready = (
        len(missing_packages) == 0
        and len(mismatched_wheels) == 0
        and len(unsupported_wheels) == 0
        and len(packages) > 0
    )

    return {
        "version": 1,
        "wheelhouse": str(wheelhouse),
        "lock_source": str(req_source_path),
        "ready": ready,
        "package_count": len(packages),
        "matched_count": len(matched_packages),
        "missing_count": len(missing_packages),
        "mismatched_count": len(mismatched_wheels),
        "unsupported_count": len(unsupported_wheels),
        "matched_packages": matched_packages,
        "missing_packages": missing_packages,
        "mismatched_wheels": mismatched_wheels,
        "unsupported_wheels": unsupported_wheels,
        "total_wheel_bytes": total_bytes,
    }
