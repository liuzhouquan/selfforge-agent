"""Read-only test candidate discovery across supported languages and frameworks.

Performs static inspection of manifests without executing any code or running
repository scripts. Handles monorepo ambiguity detection.
"""

import hashlib
import json
import os
import stat
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from selfforge_agent.runtime.execution import workspace_entry_excluded

MAX_MANIFEST_BYTES = 1024 * 1024  # 1 MiB


class AmbiguousSelectionError(ValueError):
    """Raised when multiple candidate test projects exist and no explicit target was specified."""


class ProjectCandidate(BaseModel):
    """Discovered candidate project and test framework."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=256)
    cwd: str = Field(max_length=4096)
    backend: str = Field(max_length=64)
    manifest_file: str = Field(max_length=4096)
    manifest_digest: str = Field(min_length=64, max_length=64)


def _safe_read_manifest(path: Path) -> bytes | None:
    """Read a manifest file using non-blocking, nofollow regular file descriptors."""
    if path.is_symlink() or not path.is_file():
        return None
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST_BYTES:
                return None
            with open(fd, "rb", closefd=True) as handle:
                fd = -1
                return handle.read(MAX_MANIFEST_BYTES + 1)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError:
        return None


def discover_project_candidates(repository: Path, *, max_depth: int = 4) -> list[ProjectCandidate]:
    """Scan repository for test project candidates in a strictly read-only manner."""
    repo = repository.resolve()
    if not repo.is_dir():
        raise ValueError("repository must be an existing directory")

    candidates: list[ProjectCandidate] = []
    seen: set[tuple[str, str]] = set()

    def inspect_dir(current_dir: Path, rel_cwd: str, depth: int) -> None:
        if depth > max_depth:
            return

        # Check for pytest candidates
        pyproject = current_dir / "pyproject.toml"
        setup_py = current_dir / "setup.py"
        req_txt = current_dir / "requirements.txt"
        has_tests_dir = (current_dir / "tests").is_dir() or (current_dir / "test").is_dir()

        if pyproject.is_file():
            raw = _safe_read_manifest(pyproject)
            if raw:
                try:
                    data = tomllib.loads(raw.decode("utf-8", "ignore"))
                    name = data.get("project", {}).get("name") or current_dir.name
                except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError, KeyError):
                    name = current_dir.name
                digest = hashlib.sha256(raw).hexdigest()
                key = (rel_cwd, "pytest")
                if key not in seen:
                    seen.add(key)
                    candidates.append(ProjectCandidate(
                        name=name[:256],
                        cwd=rel_cwd,
                        backend="pytest",
                        manifest_file=str(pyproject.relative_to(repo)),
                        manifest_digest=digest,
                    ))
        elif (setup_py.is_file() or req_txt.is_file()) and has_tests_dir:
            m_path = setup_py if setup_py.is_file() else req_txt
            raw = _safe_read_manifest(m_path)
            if raw:
                digest = hashlib.sha256(raw).hexdigest()
                key = (rel_cwd, "pytest")
                if key not in seen:
                    seen.add(key)
                    candidates.append(ProjectCandidate(
                        name=current_dir.name[:256],
                        cwd=rel_cwd,
                        backend="pytest",
                        manifest_file=str(m_path.relative_to(repo)),
                        manifest_digest=digest,
                    ))

        # Check for Vitest candidates (package.json)
        package_json = current_dir / "package.json"
        if package_json.is_file():
            raw = _safe_read_manifest(package_json)
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8", "ignore"))
                    name = data.get("name") or current_dir.name
                    dev_deps = data.get("devDependencies", {})
                    deps = data.get("dependencies", {})
                    scripts = data.get("scripts", {})
                    has_vitest = (
                        "vitest" in dev_deps
                        or "vitest" in deps
                        or (isinstance(scripts, dict) and any("vitest" in str(v) for v in scripts.values()))
                        or (current_dir / "vitest.config.ts").is_file()
                        or (current_dir / "vitest.config.js").is_file()
                        or (current_dir / "vite.config.ts").is_file()
                        or (current_dir / "vite.config.js").is_file()
                    )
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError, KeyError):
                    name = current_dir.name
                    has_vitest = False

                if has_vitest or (current_dir / "package.json").is_file():
                    digest = hashlib.sha256(raw).hexdigest()
                    key = (rel_cwd, "vitest")
                    if key not in seen:
                        seen.add(key)
                        candidates.append(ProjectCandidate(
                            name=str(name)[:256],
                            cwd=rel_cwd,
                            backend="vitest",
                            manifest_file=str(package_json.relative_to(repo)),
                            manifest_digest=digest,
                        ))

        # Check for Go candidates (go.mod)
        go_mod = current_dir / "go.mod"
        if go_mod.is_file():
            raw = _safe_read_manifest(go_mod)
            if raw:
                module_name = current_dir.name
                for line in raw.decode("utf-8", "ignore").splitlines():
                    if line.startswith("module "):
                        module_name = line.split("module ", 1)[1].strip()
                        break
                digest = hashlib.sha256(raw).hexdigest()
                key = (rel_cwd, "go-test")
                if key not in seen:
                    seen.add(key)
                    candidates.append(ProjectCandidate(
                        name=module_name[:256],
                        cwd=rel_cwd,
                        backend="go-test",
                        manifest_file=str(go_mod.relative_to(repo)),
                        manifest_digest=digest,
                    ))

        # Check for Maven candidates (pom.xml)
        pom_xml = current_dir / "pom.xml"
        if pom_xml.is_file():
            raw = _safe_read_manifest(pom_xml)
            if raw:
                art_id = current_dir.name
                try:
                    root_el = ET.fromstring(raw)
                    art_node = root_el.find(".//artifactId")
                    if art_node is not None and art_node.text:
                        art_id = art_node.text.strip()
                except (ET.ParseError, UnicodeDecodeError, ValueError):
                    art_id = current_dir.name
                digest = hashlib.sha256(raw).hexdigest()
                key = (rel_cwd, "maven-surefire")
                if key not in seen:
                    seen.add(key)
                    candidates.append(ProjectCandidate(
                        name=art_id[:256],
                        cwd=rel_cwd,
                        backend="maven-surefire",
                        manifest_file=str(pom_xml.relative_to(repo)),
                        manifest_digest=digest,
                    ))

        # Check for Gradle candidates (build.gradle, build.gradle.kts)
        build_gradle = current_dir / "build.gradle"
        build_gradle_kts = current_dir / "build.gradle.kts"
        if build_gradle.is_file() or build_gradle_kts.is_file():
            target_g = build_gradle if build_gradle.is_file() else build_gradle_kts
            raw = _safe_read_manifest(target_g)
            if raw:
                digest = hashlib.sha256(raw).hexdigest()
                key = (rel_cwd, "gradle-test")
                if key not in seen:
                    seen.add(key)
                    candidates.append(ProjectCandidate(
                        name=current_dir.name[:256],
                        cwd=rel_cwd,
                        backend="gradle-test",
                        manifest_file=str(target_g.relative_to(repo)),
                        manifest_digest=digest,
                    ))

        # Recursively search subdirectories
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if workspace_entry_excluded(entry.name):
                            continue
                        sub_path = Path(entry.path)
                        sub_rel = str(sub_path.relative_to(repo))
                        inspect_dir(sub_path, sub_rel, depth + 1)
        except OSError:
            pass

    inspect_dir(repo, ".", 0)
    return sorted(candidates, key=lambda c: (c.cwd, c.backend))


def resolve_project_candidate(
    repository: Path,
    cwd: str | None = None,
    backend: str | None = None,
) -> ProjectCandidate:
    """Resolve a single project candidate or raise AmbiguousSelectionError if ambiguous."""
    candidates = discover_project_candidates(repository)
    if not candidates:
        raise ValueError(f"No test project candidates discovered in repository {repository}")

    filtered = candidates
    if cwd is not None:
        norm_cwd = "." if cwd in ("", ".") else str(Path(cwd))
        filtered = [c for c in filtered if c.cwd == norm_cwd]

    if backend is not None:
        filtered = [c for c in filtered if c.backend == backend]

    if len(filtered) == 1:
        return filtered[0]

    if not filtered:
        raise ValueError(
            f"No matching test project candidate for cwd={cwd!r}, backend={backend!r}. "
            f"Available: {[f'{c.cwd}:{c.backend}' for c in candidates]}"
        )

    # More than 1 match -> Ambiguity
    summary = ", ".join(f"{c.name} (cwd={c.cwd}, backend={c.backend})" for c in filtered)
    raise AmbiguousSelectionError(
        f"Multiple candidate test projects discovered: {summary}. "
        "Explicit cwd or backend specification is required."
    )
