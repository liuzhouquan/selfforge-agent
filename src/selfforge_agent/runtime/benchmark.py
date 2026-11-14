"""Repeatable Coding Agent evaluation against pinned local Git repositories."""

import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from selfforge_agent.models import TestResult
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor, resolve_docker_image
from selfforge_agent.runtime.failure_artifacts import prepare_artifacts, write_failure
from selfforge_agent.runtime.model import ChatModel
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry, _protected
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_HOLDOUT_BYTES = 1024 * 1024
MAX_REFERENCE_BYTES = 256 * 1024

FAILURE_REASON_TAXONOMY: tuple[str, ...] = (
    "agent_max_steps",
    "agent_timed_out",
    "agent_failed",
    "agent_cancelled",
    "no_code_change",
    "unexpected_file_changes",
    "revision_changed",
    "visible_test_missing",
    "visible_test_timeout",
    "visible_test_collection_error",
    "visible_test_assertion_failed",
    "visible_test_environment_error",
    "holdout_timeout",
    "holdout_collection_error",
    "holdout_assertion_failed",
    "holdout_environment_error",
    "harness_error",
)

AGENT_ERROR_CODE_TAXONOMY: tuple[str, ...] = (
    "provider_http_<status>",
    "provider_unavailable",
    "model_finish_reason",
    "runtime_error",
    "unknown",
)


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    repository: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    task: str = Field(min_length=1, max_length=8_000)
    allowed_changes: list[str] = Field(min_length=1)
    holdout_test: str = Field(min_length=1)
    reference_patch: str | None = Field(default=None, min_length=1)
    agent_test_path: str = "tests"
    agent_test_keyword: str | None = Field(default=None, min_length=1, max_length=200)
    image: str | None = None


class BenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    cases: list[BenchmarkCase] = Field(min_length=1)


@dataclass(frozen=True)
class PreparedCase:
    spec: BenchmarkCase
    repository: Path
    holdout_bytes: bytes
    holdout_sha256: str
    reference_bytes: bytes | None = None
    reference_sha256: str | None = None


@dataclass(frozen=True)
class BenchmarkOutcome:
    case: str
    repeat: int
    success: bool
    reason: str
    agent_status: str
    changed_files: list[str]
    holdout_status: str
    holdout_passed: int
    holdout_failed: int
    holdout_errors: int
    holdout_exit_code: int | None
    duration_seconds: float
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    agent_error_code: str = ""
    agent_steps: int = 0
    tools_used: int = 0
    tool_trace: list[dict[str, str | bool]] = field(default_factory=list)
    visible_status: str = ""
    visible_passed: int = 0
    visible_failed: int = 0
    visible_errors: int = 0
    visible_exit_code: int | None = None
    failure_artifact_id: str = ""
    failure_artifact_error: str = ""
    cost_usd: float | None = None


def _git(*args: str, cwd: Path | None = None, timeout: float = 120) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Git operation failed: {exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-500:] or "Git operation failed")
    return result.stdout


def _read_regular_file(path: Path, max_bytes: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Cannot read {label} at {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"{label} must be a regular file, not a symlink, directory, or special file: {path}")
        if st.st_size > max_bytes:
            raise ValueError(f"{label} file exceeds size limit ({max_bytes} bytes): {path}")
        contents = b""
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            contents += chunk
            remaining -= len(chunk)
        if len(contents) > max_bytes:
            raise ValueError(f"{label} file exceeds size limit ({max_bytes} bytes): {path}")
        return contents
    finally:
        os.close(fd)


def _safe_relative(value: str) -> str:
    path = Path(value)
    if (not value or path.is_absolute() or value in {".", ".."} or path.as_posix() != value
            or ".." in path.parts
            or any(_protected(part) for part in path.parts)):
        raise ValueError(f"Unsafe benchmark path: {value}")
    return path.as_posix()


def load_suite(manifest: Path, *, _contents: bytes | None = None,
               _assets: dict[str, bytes] | None = None
               ) -> tuple[BenchmarkSuite, list[PreparedCase]]:
    manifest = manifest.expanduser()
    if manifest.is_symlink():
        raise ValueError(f"Manifest must be a regular file, not a symlink: {manifest}")
    manifest_bytes = _contents if _contents is not None else _read_regular_file(manifest, MAX_MANIFEST_BYTES, "Manifest")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest file exceeds size limit")
    manifest = manifest.resolve()
    suite = BenchmarkSuite.model_validate_json(manifest_bytes)
    ids: set[str] = set()
    prepared = []
    for case in suite.cases:
        if case.id in ids:
            raise ValueError(f"Duplicate benchmark case ID: {case.id}")
        ids.add(case.id)
        repository = (manifest.parent / case.repository).resolve()
        holdout_unresolved = manifest.parent / case.holdout_test
        if holdout_unresolved.is_symlink():
            raise ValueError(f"Case {case.id} holdout test must not be a symlink")
        holdout = holdout_unresolved.resolve()
        if not repository.is_dir() or not holdout.is_file() or holdout.suffix != ".py":
            raise ValueError(f"Case {case.id} needs a local Git repository and Python holdout test")
        if holdout.is_relative_to(repository):
            raise ValueError(f"Case {case.id} holdout test must be outside the agent repository")
        if Path(_git("rev-parse", "--show-toplevel", cwd=repository).strip()).resolve() != repository:
            raise ValueError(f"Case {case.id} must name the Git repository root")
        revision = _git("rev-parse", "--verify", f"{case.revision}^{{commit}}",
                        cwd=repository).strip()
        if revision != case.revision:
            raise ValueError(f"Case {case.id} revision is not a commit")
        _safe_relative(case.agent_test_path)
        if case.agent_test_keyword is not None and not case.agent_test_keyword.strip():
            raise ValueError(f"Case {case.id} has a blank test keyword")
        if case.image is not None and (not case.image or any(char.isspace() for char in case.image)):
            raise ValueError(f"Case {case.id} has an invalid Docker image reference")
        for changed in case.allowed_changes:
            _safe_relative(changed)
        if len(set(case.allowed_changes)) != len(case.allowed_changes):
            raise ValueError(f"Case {case.id} repeats an allowed change path")
        holdout_bytes = _assets[case.holdout_test] if _assets is not None else _read_regular_file(holdout_unresolved, MAX_HOLDOUT_BYTES, f"Case {case.id} holdout")
        if len(holdout_bytes) > MAX_HOLDOUT_BYTES:
            raise ValueError(f"Case {case.id} holdout file exceeds size limit")
        reference_bytes = None
        if case.reference_patch is not None:
            reference_unresolved = manifest.parent / case.reference_patch
            if reference_unresolved.is_symlink():
                raise ValueError(f"Case {case.id} reference patch must not be a symlink")
            reference = reference_unresolved.resolve()
            if (not reference.is_file() or reference.suffix not in {".patch", ".diff"}
                    or reference.is_relative_to(repository)):
                raise ValueError(f"Case {case.id} needs a reference patch outside the repository")
            if _assets is None:
                reference_bytes = _read_regular_file(reference_unresolved, MAX_REFERENCE_BYTES, f"Case {case.id} reference patch")
            else:
                reference_bytes = _assets[case.reference_patch]
            if not reference_bytes or len(reference_bytes) > MAX_REFERENCE_BYTES:
                raise ValueError(f"Case {case.id} reference patch must contain 1-262144 bytes")
        prepared.append(PreparedCase(case, repository, holdout_bytes,
                                     hashlib.sha256(holdout_bytes).hexdigest(), reference_bytes,
                                     hashlib.sha256(reference_bytes).hexdigest()
                                     if reference_bytes is not None else None))
    return suite, prepared


def _changed_files(workspace: Path) -> list[str]:
    tracked = _git("diff", "--name-only", "-z", "HEAD", cwd=workspace)
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=workspace)
    generated = {"__pycache__", ".pytest_cache", ".ruff_cache"}
    return sorted(path for path in set((tracked + untracked).strip("\0").split("\0")) - {""}
                  if not (generated & set(Path(path).parts)) and not path.endswith(".pyc"))


def _usage(session: SessionStore) -> tuple[str, str, int, int, int]:
    metrics = session.token_metrics()
    providers = metrics["providers"]
    provider = providers[0] if len(providers) == 1 else "mixed" if providers else ""
    return (
        provider,
        metrics.get("model") or ("mixed" if metrics["models"] else ""),
        metrics["prompt_tokens"],
        metrics["completion_tokens"],
        metrics["total_tokens"],
    )


def _tool_trace(session: SessionStore) -> list[dict[str, str | bool]]:
    """Keep actionable tool metadata without model messages or tool output."""
    known = {"read_file", "edit_file", "search_code", "bash", "run_tests"}
    events = [event for event in session.trace_events() if isinstance(event, dict) and event.get("event") == "tool"]
    return [{"name": event["name"] if isinstance(event.get("name"), str)
             and event["name"] in known else "unknown",
             "ok": event.get("ok") is True} for event in events[-100:]]


def _reason(agent_status: str, changed: list[str], allowed: set[str],
            visible_status: str, visible_passed: int, visible_failed: int,
            visible_errors: int, holdout_status: str, passed: int, failed: int,
            errors: int) -> str:
    if agent_status in {"failed", "timed_out", "cancelled", "max_steps"}:
        return f"agent_{agent_status}"
    if not changed:
        return "no_code_change"
    if not set(changed) <= allowed:
        return "unexpected_file_changes"
    if agent_status != "completed":
        return f"agent_{agent_status}"
    if visible_status == "missing":
        return "visible_test_missing"
    if visible_status == "timeout":
        return "visible_test_timeout"
    if visible_errors:
        return "visible_test_collection_error"
    if visible_failed:
        return "visible_test_assertion_failed"
    if visible_status != "passed" or visible_passed == 0:
        return "visible_test_environment_error"
    if holdout_status == "timeout":
        return "holdout_timeout"
    if errors:
        return "holdout_collection_error"
    if failed:
        return "holdout_assertion_failed"
    if holdout_status != "passed" or passed == 0:
        return "holdout_environment_error"
    return "passed"


def _agent_error_code(error: str) -> str:
    """Keep provider/runtime failure categories without publishing response bodies."""
    if not error:
        return "unknown"
    if error.startswith("All providers failed:"):
        statuses = re.findall(r"Provider returned HTTP (\d{3})", error)
        return f"provider_http_{statuses[-1]}" if statuses else "provider_unavailable"
    if error.startswith("Model stopped with "):
        return "model_finish_reason"
    return "runtime_error"


def _checkout(case: PreparedCase, workspace: Path) -> None:
    _git("clone", "--quiet", "--no-local", "--no-checkout", str(case.repository),
         str(workspace))
    _git("checkout", "--quiet", "--detach", case.spec.revision, cwd=workspace)
    test_path = (workspace / case.spec.agent_test_path).resolve()
    if not test_path.is_relative_to(workspace.resolve()) or not test_path.exists():
        raise ValueError(f"Case {case.spec.id} agent test path does not exist at its revision")


def _image_id(image: str) -> str:
    return resolve_docker_image(image)


def _safe_provider_url(value: object) -> str:
    """Keep public origins and standard API paths; omit credential-bearing URL components."""
    try:
        parts = urlsplit(str(value))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname
        host = f"[{host}]" if ":" in host else host
        if parts.port is not None:
            host += f":{parts.port}"
        path = parts.path if parts.path in {"", "/", "/v1", "/api", "/api/v1", "/openai/v1"} else ""
        return urlunsplit((parts.scheme, host, path, "", ""))
    except ValueError:
        return ""


def _detect_app_commit_sha() -> str | None:
    """Detect the commit SHA of the running selfforge-agent application."""
    for variable in ("SELFFORGE_APP_COMMIT_SHA", "SELFFORGE_COMMIT_SHA"):
        clean = os.environ.get(variable, "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", clean):
            return clean

    source_root = Path(__file__).resolve().parents[3]
    candidates = [source_root] if (source_root / ".git").exists() else []
    for repo_dir in candidates:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                sha = result.stdout.strip().lower()
                if re.fullmatch(r"[0-9a-f]{40}", sha):
                    return sha
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _detect_app_dirty() -> bool | None:
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True, check=False, timeout=5)
        return bool(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _detect_docker_version(timeout: float = 5.0) -> str | None:
    """Detect the Docker engine or client version string if available."""
    for cmd in (
        ["docker", "version", "--format", "{{.Server.Version}}"],
        ["docker", "--version"],
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            if result.returncode == 0:
                raw = result.stdout.strip()
                if raw:
                    match = re.search(r"version\s+([0-9a-zA-Z.-]+)", raw, re.IGNORECASE)
                    if match:
                        return match.group(1).rstrip(",")
                    return raw
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _verification_copy(workspace: Path, destination: Path) -> None:
    base_ignore = shutil.ignore_patterns(
        ".git", ".venv", ".env", ".env.*", ".local", ".selfforge",
        ".workspaces", ".aws", ".ssh", ".npmrc", ".pypirc",
        ".netrc", "__pycache__", ".pytest_cache",
    )

    def _ignore_special(directory: str, names: list[str]) -> set[str]:
        ignored = set(base_ignore(directory, names))
        for name in names:
            if name in ignored:
                continue
            item_path = os.path.join(directory, name)
            try:
                st = os.lstat(item_path)
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                    ignored.add(name)
            except OSError:
                ignored.add(name)
        return ignored

    shutil.copytree(workspace, destination, symlinks=True, ignore=_ignore_special)


def _install_holdout(destination: Path, case: PreparedCase) -> str:
    holdout_path = Path("tests") / f"test_selfforge_holdout_{case.spec.id}.py"
    target = destination / holdout_path
    if target.is_symlink() or target.exists():
        raise ValueError("Repository already contains the reserved holdout test path")
    root_fd = tests_fd = -1
    created = False
    try:
        root_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.mkdir("tests", dir_fd=root_fd)
        except FileExistsError:
            pass
        tests_fd = os.open("tests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o644, dir_fd=tests_fd)
        created = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(case.holdout_bytes)
    except OSError as exc:
        if created:
            os.unlink(target.name, dir_fd=tests_fd)
        raise ValueError("Cannot install holdout test safely; directory or target may be a symlink") from exc
    finally:
        if tests_fd >= 0:
            os.close(tests_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return holdout_path.as_posix()


def _runner(sandbox: Literal["docker", "host"], image: str) -> DockerTestRunner | TestRunner:
    return DockerTestRunner(image) if sandbox == "docker" else TestRunner()


def _baseline(case: PreparedCase, *, sandbox: Literal["docker", "host"],
              trusted_host: bool, image: str, test_timeout: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="selfforge-baseline-") as temp:
        root = Path(temp)
        workspace = root / "source"
        _checkout(case, workspace)
        _verification_copy(workspace, root / "verification")
        path = _install_holdout(root / "verification", case)
        result = _runner(sandbox, image).run(root / "verification", trusted=trusted_host,
                                              timeout=test_timeout, test_path=path)
    if result.status != "failed" or result.failed == 0 or result.errors:
        raise ValueError(f"Case {case.spec.id} needs a failing assertion on its pinned baseline; "
                         f"got {result.status}, failed={result.failed}, errors={result.errors}")
    return {"revision": case.spec.revision, "holdout_sha256": case.holdout_sha256,
            "status": result.status, "failed": result.failed}


def _test_summary(result: TestResult) -> dict:
    return {name: getattr(result, name) for name in
            ("status", "passed", "failed", "errors", "skipped", "exit_code")}


def _reference_check(case: PreparedCase, *, sandbox: Literal["docker", "host"],
                     trusted_host: bool, image: str, test_timeout: float) -> dict:
    if case.reference_bytes is None:
        raise ValueError(f"Case {case.spec.id} has no reference patch")
    with tempfile.TemporaryDirectory(prefix="selfforge-reference-") as temp:
        root = Path(temp)
        workspace = root / "source"
        _checkout(case, workspace)
        patch = root / "reference.patch"
        patch.write_bytes(case.reference_bytes)
        try:
            stats = _git("apply", "--numstat", "-z", str(patch), cwd=workspace, timeout=10)
            paths = []
            for record in stats.split("\0"):
                if not record:
                    continue
                fields = record.split("\t", 2)
                if (len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit()
                        or _safe_relative(fields[2]) not in case.spec.allowed_changes):
                    raise ValueError("Reference must be an allowed text patch; renames are unsupported")
                paths.append(fields[2])
            if not paths:
                raise ValueError("Reference patch must change an allowed file")
            _git("apply", "--check", str(patch), cwd=workspace, timeout=10)
            _git("apply", str(patch), cwd=workspace, timeout=10)
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f"Case {case.spec.id} reference patch has invalid paths or cannot apply") from exc
        changed = _changed_files(workspace)
        if not changed or not set(changed) <= set(case.spec.allowed_changes):
            raise ValueError(f"Case {case.spec.id} reference changed unexpected files")
        summaries = {}
        runner = _runner(sandbox, image)
        for phase in ("visible", "holdout"):
            # Separate copies prevent visible tests from modifying holdout inputs.
            verification = root / phase
            _verification_copy(workspace, verification)
            path = (case.spec.agent_test_path if phase == "visible"
                    else _install_holdout(verification, case))
            result = runner.run(verification, trusted=trusted_host, timeout=test_timeout,
                                test_path=path,
                                keyword=case.spec.agent_test_keyword if phase == "visible" else None)
            summaries[phase] = _test_summary(result)
            if (result.status != "passed" or result.exit_code != 0 or result.passed == 0
                    or result.failed or result.errors):
                raise ValueError(f"Case {case.spec.id} reference {phase} tests did not pass: "
                                 f"{summaries[phase]}")
    return {"status": "validated", "patch_sha256": case.reference_sha256,
            "changed_files": changed, **summaries}


def _preflight(manifest: Path, *, sandbox: Literal["docker", "host"], trusted_host: bool,
               image: str, test_timeout: float, require_references: bool = False,
               manifest_contents: bytes | None = None, asset_contents: dict[str, bytes] | None = None
               ) -> tuple[bytes, BenchmarkSuite, list[PreparedCase], dict, dict]:
    if sandbox not in {"docker", "host"} or test_timeout <= 0:
        raise ValueError("Invalid benchmark sandbox or test timeout")
    if sandbox == "host" and not trusted_host:
        raise ValueError("Host benchmark execution requires explicit trust")
    manifest = manifest.expanduser()
    if manifest.is_symlink():
        raise ValueError(f"Manifest must be a regular file, not a symlink: {manifest}")
    manifest_bytes = manifest_contents if manifest_contents is not None else _read_regular_file(manifest, MAX_MANIFEST_BYTES, "Manifest")
    manifest = manifest.resolve()
    suite, cases = load_suite(manifest, _contents=manifest_bytes, _assets=asset_contents)
    if require_references and any(case.reference_bytes is None for case in cases):
        raise ValueError("Task validation requires a reference_patch for every case")
    images = {}
    if sandbox == "docker":
        resolved_images = {}
        for case in cases:
            reference = case.spec.image or image
            if reference not in resolved_images:
                resolved_images[reference] = _image_id(reference)
            images[case.spec.id] = {"reference": reference, "id": resolved_images[reference]}
    baselines = {}
    for case in cases:
        options = {"sandbox": sandbox, "trusted_host": trusted_host,
                   "image": images[case.spec.id]["id"] if images else image,
                   "test_timeout": test_timeout}
        baseline = _baseline(case, **options)
        if case.reference_bytes is not None:
            baseline["reference"] = _reference_check(case, **options)
        baselines[case.spec.id] = baseline
    return manifest_bytes, suite, cases, images, baselines


def validate_suite(manifest: Path, *, sandbox: Literal["docker", "host"] = "docker",
                   trusted_host: bool = False, image: str = "selfforge-agent-sandbox:latest",
                   test_timeout: float = 60, manifest_contents: bytes | None = None,
                   asset_contents: dict[str, bytes] | None = None) -> dict:
    """Check task solvability without constructing or calling a model."""
    contents, suite, _, images, baselines = _preflight(
        manifest, sandbox=sandbox, trusted_host=trusted_host, image=image,
        test_timeout=test_timeout, require_references=True,
        manifest_contents=manifest_contents, asset_contents=asset_contents)
    return {"version": 1, "status": "validated", "cases": len(suite.cases),
            "manifest_sha256": hashlib.sha256(contents).hexdigest(),
            "sandbox": sandbox, "images": images, "baselines": baselines}


def _run_once(case: PreparedCase, repeat: int, model_factory: Callable[[BenchmarkCase], ChatModel],
              *, sandbox: Literal["docker", "host"], trusted_host: bool, image: str,
              max_steps: int, time_budget: float, test_timeout: float,
              failure_artifacts: Path | None = None, context_budget: int = 8000) -> BenchmarkOutcome:
    started = time.monotonic()
    agent_status = visible_status = holdout_status = provider = model = ""
    changed: list[str] = []
    prompt = completion = total = passed = failed = errors = 0
    exit_code = None
    visible_passed = visible_failed = visible_errors = 0
    visible_exit_code = None
    visible = evidence = None
    artifact_id = artifact_error = ""
    reason = "harness_error"
    agent_error_code = ""
    agent_steps = tools_used = 0
    tool_trace: list[dict[str, str | bool]] = []
    session: SessionStore | None = None
    cost_usd = None
    try:
        with tempfile.TemporaryDirectory(prefix="selfforge-benchmark-") as temp:
            root = Path(temp)
            workspace = root / "agent"
            _checkout(case, workspace)
            session = SessionStore(root / "session")
            executor = (DockerExecutor(workspace, image=image) if sandbox == "docker"
                        else HostExecutor(workspace, trusted=trusted_host))
            agent = CodingAgent(model_factory(case.spec), ToolRegistry(workspace, executor),
                                session, max_steps=max_steps, time_budget=time_budget,
                                context_budget=context_budget)
            task = (case.spec.task + "\n\nAfter the final edit, call run_tests with path="
                    + repr(case.spec.agent_test_path))
            if case.spec.agent_test_keyword is not None:
                task += ", keyword=" + repr(case.spec.agent_test_keyword)
            task += "."
            result = agent.run(task)
            agent_status = result.status
            agent_steps, tools_used = result.steps, result.tools_used
            if result.status == "failed":
                agent_error_code = _agent_error_code(result.error)
            provider, model, prompt, completion, total = _usage(session)
            try:
                cost = session.token_metrics().get("cost")
                cost_usd = cost["total_cost_usd"] if cost else None
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                # Optional telemetry must not replace the primary harness outcome.
                pass
            tool_trace = _tool_trace(session)
            if _git("rev-parse", "HEAD", cwd=workspace).strip() != case.spec.revision:
                reason = "revision_changed"
            else:
                changed = _changed_files(workspace)
                verification = root / "verification"
                holdout_verification = root / "holdout"
                _verification_copy(workspace, verification)
                _verification_copy(workspace, holdout_verification)
                visible_path = (verification / case.spec.agent_test_path).resolve()
                if not visible_path.is_relative_to(verification) or not visible_path.exists():
                    visible_status = "missing"
                else:
                    visible = _runner(sandbox, image).run(
                        verification, trusted=trusted_host, timeout=test_timeout,
                        test_path=case.spec.agent_test_path,
                        keyword=case.spec.agent_test_keyword)
                    visible_status = visible.status
                    visible_passed, visible_failed, visible_errors, visible_exit_code = (
                        visible.passed, visible.failed, visible.errors, visible.exit_code)
                holdout_path = _install_holdout(holdout_verification, case)
                evidence = _runner(sandbox, image).run(
                    holdout_verification, trusted=trusted_host,
                    timeout=test_timeout, test_path=holdout_path)
                holdout_status = evidence.status
                passed, failed, errors, exit_code = (evidence.passed, evidence.failed,
                                                     evidence.errors, evidence.exit_code)
                reason = _reason(agent_status, changed, set(case.spec.allowed_changes),
                                 visible_status, visible_passed, visible_failed, visible_errors,
                                 holdout_status, passed, failed, errors)
    except (OSError, RuntimeError, ValueError) as exc:
        reason = f"harness_error:{type(exc).__name__}"
    if reason != "passed" and failure_artifacts is not None:
        try:
            artifact_id = write_failure(failure_artifacts, case=case.spec.id, repeat=repeat,
                                        reason=reason, visible=visible, holdout=evidence)
        except (OSError, ValueError):
            artifact_error = "artifact_write_failed"
    return BenchmarkOutcome(case.spec.id, repeat, reason == "passed", reason, agent_status,
                            changed, holdout_status, passed, failed, errors, exit_code,
                            round(time.monotonic() - started, 3), provider, model,
                            prompt, completion, total, agent_error_code,
                            agent_steps, tools_used, tool_trace, visible_status,
                            visible_passed, visible_failed, visible_errors, visible_exit_code,
                            artifact_id, artifact_error, cost_usd)


def evaluate_suite(manifest: Path, model_factory: Callable[[BenchmarkCase], ChatModel], *,
                   repeats: int = 1, sandbox: Literal["docker", "host"] = "docker",
                   trusted_host: bool = False, image: str = "selfforge-agent-sandbox:latest",
                   max_steps: int = 12, time_budget: float = 300,
                   test_timeout: float = 60, failure_artifacts: Path | None = None,
                   providers: Sequence[Any] | None = None, context_budget: int = 8000,
                   manifest_contents: bytes | None = None,
                   asset_contents: dict[str, bytes] | None = None) -> dict:
    """Run each pinned case from scratch; verify with an unseen external pytest file."""
    if not 1 <= repeats <= 20 or not 1 <= max_steps <= 100 or time_budget <= 0 or test_timeout <= 0:
        raise ValueError("Invalid benchmark repeat, step, or time limit")
    if context_budget < 1000:
        raise ValueError("Invalid benchmark context budget")
    started_at = datetime.now(UTC).isoformat()
    sanitized_providers: list[dict[str, str]] = []
    if providers is not None:
        for p in providers:
            if isinstance(p, dict):
                sanitized_providers.append({
                    "name": str(p.get("name", "")),
                    "model": str(p.get("model", "")),
                    "base_url": _safe_provider_url(p.get("base_url", "")),
                })
            else:
                sanitized_providers.append({
                    "name": str(getattr(p, "name", "")),
                    "model": str(getattr(p, "model", "")),
                    "base_url": _safe_provider_url(getattr(p, "base_url", "")),
                })
    commit_sha = _detect_app_commit_sha()
    provenance = {
        "commit_sha": commit_sha,
        "app_commit_sha": commit_sha,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "docker_version": _detect_docker_version() if sandbox == "docker" else None,
        "budgets": {
            "max_steps": max_steps,
            "time_budget": time_budget,
            "test_timeout": test_timeout,
            "repeats": repeats,
        },
        "providers": sanitized_providers,
        "app_dirty": _detect_app_dirty(),
        "app_commit_source": ("environment_declared" if any(re.fullmatch(r"[0-9a-fA-F]{40}", os.environ.get(k, "").strip()) for k in ("SELFFORGE_APP_COMMIT_SHA", "SELFFORGE_COMMIT_SHA")) else "source_git" if commit_sha else "unavailable"),
        "docker_version_scope": "engine_or_client" if sandbox == "docker" else None,
        "context_budget": context_budget,
        "provider_config_source": "configured" if providers is not None else "observed",
        "started_at": started_at,
        "completed_at": None,
    }
    manifest_bytes, suite, cases, images, baselines = _preflight(
        manifest, sandbox=sandbox, trusted_host=trusted_host, image=image,
        test_timeout=test_timeout, manifest_contents=manifest_contents, asset_contents=asset_contents)
    artifact_root = (prepare_artifacts(failure_artifacts, [case.repository for case in cases])
                     if failure_artifacts is not None else None)
    outcomes = [_run_once(case, repeat, model_factory, sandbox=sandbox,
                          trusted_host=trusted_host,
                          image=images[case.spec.id]["id"] if images else image,
                          max_steps=max_steps,
                          time_budget=time_budget, test_timeout=test_timeout,
                          failure_artifacts=artifact_root, context_budget=context_budget)
                for case in cases for repeat in range(1, repeats + 1)]
    completed_at = datetime.now(UTC).isoformat()
    provenance["completed_at"] = completed_at
    successes = sum(item.success for item in outcomes)
    counts = Counter(item.reason for item in outcomes if not item.success)
    agent_errors = Counter(item.agent_error_code for item in outcomes if item.agent_error_code)
    image_ids = {item["id"] for item in images.values()}
    by_case = {case.spec.id: {
        "successes": sum(item.success for item in outcomes if item.case == case.spec.id),
        "runs": repeats,
    } for case in cases}
    if providers is None:
        seen = set()
        for item in outcomes:
            key = (item.provider, item.model)
            if key not in seen and (item.provider or item.model):
                seen.add(key)
                sanitized_providers.append({
                    "name": item.provider,
                    "model": item.model,
                    "base_url": "",
                })
    return {
        "version": 1,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "failure_artifact_run": artifact_root.name if artifact_root is not None else None,
        "cases": len(suite.cases), "repeats": repeats, "runs": len(outcomes),
        "successes": successes, "success_rate": successes / len(outcomes),
        "sandbox": sandbox, "image": image if sandbox == "docker" else None,
        "image_id": next(iter(image_ids)) if len(image_ids) == 1 else None,
        "images": images,
        "baselines": baselines,
        "failure_reasons": dict(sorted(counts.items())),
        "agent_error_codes": dict(sorted(agent_errors.items())), "by_case": by_case,
        "total_duration_seconds": round(sum(item.duration_seconds for item in outcomes), 3),
        "prompt_tokens": sum(item.prompt_tokens for item in outcomes),
        "completion_tokens": sum(item.completion_tokens for item in outcomes),
        "total_tokens": sum(item.total_tokens for item in outcomes),
        "total_cost_usd": (
            round(sum(c for c in (item.cost_usd for item in outcomes) if c is not None), 6)
            if outcomes and all(item.cost_usd is not None for item in outcomes)
            else None
        ),
        "results": [asdict(item) for item in outcomes],
        "provenance": provenance,
    }
