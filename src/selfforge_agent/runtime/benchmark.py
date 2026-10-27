"""Repeatable Coding Agent evaluation against pinned local Git repositories."""

import hashlib
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ChatModel
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry, _protected
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    repository: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    task: str = Field(min_length=1, max_length=8_000)
    allowed_changes: list[str] = Field(min_length=1)
    holdout_test: str = Field(min_length=1)
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


def _git(*args: str, cwd: Path | None = None, timeout: float = 120) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Git operation failed: {exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-500:] or "Git operation failed")
    return result.stdout


def _safe_relative(value: str) -> str:
    path = Path(value)
    if (not value or path.is_absolute() or value in {".", ".."} or path.as_posix() != value
            or ".." in path.parts
            or any(_protected(part) for part in path.parts)):
        raise ValueError(f"Unsafe benchmark path: {value}")
    return path.as_posix()


def load_suite(manifest: Path, *, _contents: bytes | None = None
               ) -> tuple[BenchmarkSuite, list[PreparedCase]]:
    manifest = manifest.expanduser().resolve()
    suite = BenchmarkSuite.model_validate_json(
        manifest.read_bytes() if _contents is None else _contents)
    ids: set[str] = set()
    prepared = []
    for case in suite.cases:
        if case.id in ids:
            raise ValueError(f"Duplicate benchmark case ID: {case.id}")
        ids.add(case.id)
        repository = (manifest.parent / case.repository).resolve()
        holdout = (manifest.parent / case.holdout_test).resolve()
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
        holdout_bytes = holdout.read_bytes()
        prepared.append(PreparedCase(case, repository, holdout_bytes,
                                     hashlib.sha256(holdout_bytes).hexdigest()))
    return suite, prepared


def _changed_files(workspace: Path) -> list[str]:
    tracked = _git("diff", "--name-only", "-z", "HEAD", cwd=workspace)
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=workspace)
    generated = {"__pycache__", ".pytest_cache", ".ruff_cache"}
    return sorted(path for path in set((tracked + untracked).strip("\0").split("\0")) - {""}
                  if not (generated & set(Path(path).parts)) and not path.endswith(".pyc"))


def _usage(session: SessionStore) -> tuple[str, str, int, int, int]:
    provider = model = ""
    prompt = completion = total = 0
    for event in session.trace_events():
        if event.get("event") != "model_call":
            continue
        provider = str(event.get("provider") or provider)
        model = str(event.get("model") or model)
        usage = event.get("usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        total += int(usage.get("total_tokens") or 0)
    return provider, model, prompt, completion, total


def _tool_trace(session: SessionStore) -> list[dict[str, str | bool]]:
    """Keep actionable tool metadata without model messages or tool output."""
    known = {"read_file", "edit_file", "search_code", "bash", "run_tests"}
    events = [event for event in session.trace_events() if event.get("event") == "tool"]
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
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Docker image inspection failed: {exc}") from exc
    if result.returncode or not result.stdout.strip():
        raise ValueError(f"Docker image is unavailable: {image}")
    return result.stdout.strip()


def _verification_copy(workspace: Path, destination: Path) -> None:
    shutil.copytree(workspace, destination, symlinks=True,
                    ignore=shutil.ignore_patterns(
                        ".git", ".venv", ".env", ".env.*", ".local", ".selfforge",
                        ".workspaces", ".aws", ".ssh", ".npmrc", ".pypirc",
                        ".netrc", "__pycache__", ".pytest_cache"))


def _install_holdout(destination: Path, case: PreparedCase) -> str:
    holdout_path = Path("tests") / f"test_selfforge_holdout_{case.spec.id}.py"
    target = destination / holdout_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("Repository already contains the reserved holdout test path")
    target.write_bytes(case.holdout_bytes)
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


def _run_once(case: PreparedCase, repeat: int, model_factory: Callable[[BenchmarkCase], ChatModel],
              *, sandbox: Literal["docker", "host"], trusted_host: bool, image: str,
              max_steps: int, time_budget: float, test_timeout: float) -> BenchmarkOutcome:
    started = time.monotonic()
    agent_status = visible_status = holdout_status = provider = model = ""
    changed: list[str] = []
    prompt = completion = total = passed = failed = errors = 0
    exit_code = None
    visible_passed = visible_failed = visible_errors = 0
    visible_exit_code = None
    reason = "harness_error"
    agent_error_code = ""
    agent_steps = tools_used = 0
    tool_trace: list[dict[str, str | bool]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="selfforge-benchmark-") as temp:
            root = Path(temp)
            workspace = root / "agent"
            _checkout(case, workspace)
            session = SessionStore(root / "session")
            executor = (DockerExecutor(workspace, image=image) if sandbox == "docker"
                        else HostExecutor(workspace, trusted=trusted_host))
            agent = CodingAgent(model_factory(case.spec), ToolRegistry(workspace, executor),
                                session, max_steps=max_steps, time_budget=time_budget)
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
            tool_trace = _tool_trace(session)
            if _git("rev-parse", "HEAD", cwd=workspace).strip() != case.spec.revision:
                reason = "revision_changed"
            else:
                changed = _changed_files(workspace)
                verification = root / "verification"
                _verification_copy(workspace, verification)
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
                holdout_path = _install_holdout(verification, case)
                evidence = _runner(sandbox, image).run(
                    verification, trusted=trusted_host,
                    timeout=test_timeout, test_path=holdout_path)
                holdout_status = evidence.status
                passed, failed, errors, exit_code = (evidence.passed, evidence.failed,
                                                     evidence.errors, evidence.exit_code)
                reason = _reason(agent_status, changed, set(case.spec.allowed_changes),
                                 visible_status, visible_passed, visible_failed, visible_errors,
                                 holdout_status, passed, failed, errors)
    except (OSError, RuntimeError, ValueError) as exc:
        reason = f"harness_error:{type(exc).__name__}"
    return BenchmarkOutcome(case.spec.id, repeat, reason == "passed", reason, agent_status,
                            changed, holdout_status, passed, failed, errors, exit_code,
                            round(time.monotonic() - started, 3), provider, model,
                            prompt, completion, total, agent_error_code,
                            agent_steps, tools_used, tool_trace, visible_status,
                            visible_passed, visible_failed, visible_errors, visible_exit_code)


def evaluate_suite(manifest: Path, model_factory: Callable[[BenchmarkCase], ChatModel], *,
                   repeats: int = 1, sandbox: Literal["docker", "host"] = "docker",
                   trusted_host: bool = False, image: str = "selfforge-agent-sandbox:latest",
                   max_steps: int = 12, time_budget: float = 300,
                   test_timeout: float = 60) -> dict:
    """Run each pinned case from scratch; verify with an unseen external pytest file."""
    if not 1 <= repeats <= 20 or not 1 <= max_steps <= 100 or time_budget <= 0 or test_timeout <= 0:
        raise ValueError("Invalid benchmark repeat, step, or time limit")
    if sandbox not in {"docker", "host"}:
        raise ValueError("Benchmark sandbox must be docker or host")
    if sandbox == "host" and not trusted_host:
        raise ValueError("Host benchmark execution requires explicit trust")
    manifest = manifest.expanduser().resolve()
    manifest_bytes = manifest.read_bytes()
    suite, cases = load_suite(manifest, _contents=manifest_bytes)
    images = {}
    if sandbox == "docker":
        resolved_images = {}
        for case in cases:
            reference = case.spec.image or image
            if reference not in resolved_images:
                resolved_images[reference] = _image_id(reference)
            images[case.spec.id] = {"reference": reference, "id": resolved_images[reference]}
    baselines = {case.spec.id: _baseline(case, sandbox=sandbox, trusted_host=trusted_host,
                                         image=images[case.spec.id]["id"] if images else image,
                                         test_timeout=test_timeout)
                 for case in cases}
    outcomes = [_run_once(case, repeat, model_factory, sandbox=sandbox,
                          trusted_host=trusted_host,
                          image=images[case.spec.id]["id"] if images else image,
                          max_steps=max_steps,
                          time_budget=time_budget, test_timeout=test_timeout)
                for case in cases for repeat in range(1, repeats + 1)]
    successes = sum(item.success for item in outcomes)
    counts = Counter(item.reason for item in outcomes if not item.success)
    agent_errors = Counter(item.agent_error_code for item in outcomes if item.agent_error_code)
    image_ids = {item["id"] for item in images.values()}
    by_case = {case.spec.id: {
        "successes": sum(item.success for item in outcomes if item.case == case.spec.id),
        "runs": repeats,
    } for case in cases}
    return {
        "version": 1,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
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
        "results": [asdict(item) for item in outcomes],
    }
