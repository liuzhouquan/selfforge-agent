"""Bind Coding sessions to one execution mode and immutable Docker image, and detect repository environment."""

import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from selfforge_agent.runtime.execution import (
    DockerExecutor,
    Executor,
    HostExecutor,
    resolve_docker_image,
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
        if filepath.is_file() and not filepath.is_symlink() and filepath.stat().st_size <= 128 * 1024:
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                if "--hash=sha256:" in content:
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
    """Extract hash-locked requirement lines (name==version --hash=sha256:...) from a uv.lock file."""
    if not lock_path.is_file():
        raise ValueError(f"Lock file does not exist: {lock_path}")
    if lock_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Lock file exceeds 5 MiB size limit")

    content = lock_path.read_bytes()
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Failed to parse lock file as TOML: {exc}") from exc

    packages = data.get("package", [])
    if not isinstance(packages, list) or not packages:
        raise ValueError("Lock file contains no packages")

    requirements: dict[str, tuple[str, str, set[str]]] = {}
    for pkg in packages:
        if not isinstance(pkg, dict):
            raise ValueError("Invalid package entry in lock file")  # noqa: TRY004 - malformed file data
        name = pkg.get("name")
        version = pkg.get("version")
        source = pkg.get("source", {})
        if isinstance(source, dict) and any(key in source for key in ("editable", "virtual", "directory")):
            continue
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", name)
                or not isinstance(version, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,199}", version)):
            raise ValueError("Invalid package name or version in lock file")

        hashes: set[str] = set()
        wheels = pkg.get("wheels", [])
        if not isinstance(wheels, list):
            raise ValueError("Invalid wheel list in lock file")  # noqa: TRY004 - malformed file data
        for wheel in wheels:
            if isinstance(wheel, dict) and "hash" in wheel:
                h = wheel["hash"]
                if isinstance(h, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                    hashes.add(h)
        sdist = pkg.get("sdist")
        if isinstance(sdist, dict) and "hash" in sdist:
            h = sdist["hash"]
            if isinstance(h, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", h):
                hashes.add(h)

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
