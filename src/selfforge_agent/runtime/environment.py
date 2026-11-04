"""Bind Coding sessions to one execution mode and immutable Docker image, and detect repository environment."""

import hashlib
import json
import re
import shlex
import time
import tomllib
from pathlib import Path

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
    parse_locked_requirements,
    read_locked_wheels,
    validate_image_tag,
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
                with filepath.open("r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read(128 * 1024)
                if ("--hash=sha256:" in content
                        or bool(re.search(r'(?:hash\s*=\s*["\'](?:sha256:)?[0-9a-fA-F]{64}["\']|["\']sha256:[0-9a-fA-F]{64}["\'])', content))):
                    has_hash_lock = True
                    break
            except OSError:
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
    """Extract hash-locked requirement lines (name==version --hash=sha256:...) from a uv.lock, poetry.lock, or Pipfile.lock file."""
    if not lock_path.is_file():
        raise ValueError(f"Lock file does not exist: {lock_path}")
    if lock_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Lock file exceeds 5 MiB size limit")

    content = _read(lock_path, 5 * 1024 * 1024)
    is_json = lock_path.name == "Pipfile.lock" or lock_path.suffix == ".json" or content.lstrip().startswith(b"{")

    requirements: dict[str, tuple[str, str, set[str]]] = {}

    if is_json:
        try:
            data = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Failed to parse lock file as JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Lock file contains no packages")

        default_sec = data.get("default")
        develop_sec = data.get("develop")
        if not isinstance(default_sec, dict) and not isinstance(develop_sec, dict):
            raise ValueError("Lock file contains no packages")
        if not default_sec and not develop_sec:
            raise ValueError("Lock file contains no packages")

        for section in ("default", "develop"):
            pkgs = data.get(section)
            if not isinstance(pkgs, dict):
                continue
            for name, pkg in pkgs.items():
                if not isinstance(pkg, dict):
                    raise ValueError("Invalid package entry in lock file")  # noqa: TRY004 - malformed file data
                if pkg.get("editable") is True or any(k in pkg for k in ("git", "file", "path", "ref")):
                    continue
                raw_ver = pkg.get("version")
                if not isinstance(raw_ver, str):
                    raise ValueError("Invalid package name or version in lock file")  # noqa: TRY004 - malformed file data
                version = raw_ver.lstrip("=")
                if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", name)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,199}", version)):
                    raise ValueError("Invalid package name or version in lock file")
                hashes: set[str] = set()
                raw_hashes = pkg.get("hashes", [])
                if isinstance(raw_hashes, list):
                    for h in raw_hashes:
                        if isinstance(h, str):
                            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                                hashes.add(h)
                            elif re.fullmatch(r"[0-9a-fA-F]{64}", h):
                                hashes.add(f"sha256:{h}")
                if not hashes:
                    raise ValueError("Lock file includes an external package without supported SHA-256 hashes")
                key = re.sub(r"[-_.]+", "-", name).lower()
                if key in requirements:
                    if requirements[key][1] != version:
                        raise ValueError("Version forks require a target-aware export; cannot flatten this lock")
                    requirements[key][2].update(hashes)
                else:
                    requirements[key] = (name, version, hashes)
    else:
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Failed to parse lock file as TOML: {exc}") from exc

        packages = data.get("package", [])
        if not isinstance(packages, list) or not packages:
            raise ValueError("Lock file contains no packages")

        metadata_files = data.get("metadata", {}).get("files", {})
        if not isinstance(metadata_files, dict):
            metadata_files = {}

        for pkg in packages:
            if not isinstance(pkg, dict):
                raise ValueError("Invalid package entry in lock file")  # noqa: TRY004 - malformed file data
            name = pkg.get("name")
            version = pkg.get("version")
            source = pkg.get("source", {})
            if isinstance(source, dict) and (
                any(key in source for key in ("editable", "virtual", "directory"))
                or source.get("type") in ("git", "directory", "editable", "virtual")
            ):
                continue
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", name)
                    or not isinstance(version, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,199}", version)):
                raise ValueError("Invalid package name or version in lock file")

            hashes = set()

            # uv.lock wheels format
            wheels = pkg.get("wheels", [])
            if isinstance(wheels, list):
                for wheel in wheels:
                    if isinstance(wheel, dict) and "hash" in wheel:
                        h = wheel["hash"]
                        if isinstance(h, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                            hashes.add(h)
            elif wheels:
                raise ValueError("Invalid wheel list in lock file")

            # uv.lock sdist format
            sdist = pkg.get("sdist")
            if isinstance(sdist, dict) and "hash" in sdist:
                h = sdist["hash"]
                if isinstance(h, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                    hashes.add(h)

            # poetry.lock files format
            files = pkg.get("files", [])
            if isinstance(files, list):
                for file_entry in files:
                    if isinstance(file_entry, dict) and "hash" in file_entry:
                        h = file_entry["hash"]
                        if isinstance(h, str):
                            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                                hashes.add(h)
                            elif re.fullmatch(r"[0-9a-fA-F]{64}", h):
                                hashes.add(f"sha256:{h}")
                    elif isinstance(file_entry, str):
                        if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", file_entry):
                            hashes.add(file_entry)
                        elif re.fullmatch(r"[0-9a-fA-F]{64}", file_entry):
                            hashes.add(f"sha256:{file_entry}")

            # poetry 1.0 metadata.files format
            if not hashes and name in metadata_files:
                meta_entries = metadata_files[name]
                if isinstance(meta_entries, list):
                    for file_entry in meta_entries:
                        if isinstance(file_entry, dict) and "hash" in file_entry:
                            h = file_entry["hash"]
                            if isinstance(h, str):
                                if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                                    hashes.add(h)
                                elif re.fullmatch(r"[0-9a-fA-F]{64}", h):
                                    hashes.add(f"sha256:{h}")
                        elif isinstance(file_entry, str):
                            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", file_entry):
                                hashes.add(file_entry)
                            elif re.fullmatch(r"[0-9a-fA-F]{64}", file_entry):
                                hashes.add(f"sha256:{file_entry}")

            if not hashes:
                raise ValueError("Lock file includes an external package without supported SHA-256 hashes")
            key = re.sub(r"[-_.]+", "-", name).lower()
            if key in requirements:
                if requirements[key][1] != version:
                    raise ValueError("Version forks require a target-aware export; cannot flatten this lock")
                requirements[key][2].update(hashes)
            else:
                requirements[key] = (name, version, hashes)

    if not requirements:
        raise ValueError("No hashed requirements found in lock file")

    lines = sorted(f"{name}=={version}" + "".join(f" --hash={h}" for h in sorted(hashes))
                   for name, version, hashes in requirements.values())
    result = "\n".join(lines) + "\n"
    if output_path is not None:
        output_path.write_text(result, encoding="utf-8")
    return result


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

    req_text = ""
    req_source_path: Path | None = None
    export_needed = False

    if requirements_file is not None:
        p = requirements_file.expanduser().absolute()
        if not p.is_file():
            raise ValueError(f"Requirements file does not exist: {requirements_file}")
        req_text = _read(p, MAX_LOCK_BYTES).decode("utf-8")
        req_source_path = p
    elif lock_file is not None:
        p = lock_file.expanduser().absolute()
        if not p.is_file():
            raise ValueError(f"Lock file does not exist: {lock_file}")
        is_structured_lock = (
            p.name in {"uv.lock", "poetry.lock", "Pipfile.lock"}
            or p.suffix in {".toml", ".json"}
            or any(kw in p.name.lower() for kw in ("poetry", "uv", "pipfile"))
        )
        if not is_structured_lock:
            try:
                with p.open("r", encoding="utf-8", errors="ignore") as h:
                    sample = h.read(4096)
                if "[[package]]" in sample or sample.lstrip().startswith("{"):
                    is_structured_lock = True
            except OSError:
                pass
        if is_structured_lock:
            req_text = export_hash_locked_requirements(p)
            export_needed = True
        else:
            req_text = _read(p, MAX_LOCK_BYTES).decode("utf-8")
        req_source_path = p
    else:
        # Check standard locations in repo
        uv_lock = repo / "uv.lock"
        poetry_lock = repo / "poetry.lock"
        pipfile_lock = repo / "Pipfile.lock"
        req_lock = repo / "requirements.lock"
        if uv_lock.is_file():
            req_text = export_hash_locked_requirements(uv_lock)
            export_needed = True
            req_source_path = uv_lock
        elif poetry_lock.is_file():
            req_text = export_hash_locked_requirements(poetry_lock)
            export_needed = True
            req_source_path = poetry_lock
        elif pipfile_lock.is_file():
            req_text = export_hash_locked_requirements(pipfile_lock)
            export_needed = True
            req_source_path = pipfile_lock
        elif req_lock.is_file():
            req_text = _read(req_lock, MAX_LOCK_BYTES).decode("utf-8")
            req_source_path = req_lock
        else:
            raise ValueError("No supported lockfile (uv.lock, poetry.lock, Pipfile.lock, requirements.lock) found in repository")

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
