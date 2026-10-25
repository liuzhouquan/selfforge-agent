"""Local web workbench and constrained review API."""

import hashlib
import os
from pathlib import Path
from threading import Event
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.coding.agent import RepairAgent
from selfforge_agent.coding.model_client import FallbackCodingModel
from selfforge_agent.coding.models import RepairReport
from selfforge_agent.coding.test_generation import (
    DifferentialTestGenerator,
    DifferentialTestReport,
    RiskTarget,
    TestGenerationBatchReport,
    validate_risk_target,
)
from selfforge_agent.coding.worktree import WorktreeError, git
from selfforge_agent.core.git_context import GitContextError
from selfforge_agent.integrations.github_pr import (
    CheckoutResult,
    GitHubPR,
    GitHubPRError,
    checkout_pr,
    load_pr,
)
from selfforge_agent.integrations.model_http import ProviderHTTPError
from selfforge_agent.models import EvidenceReport
from selfforge_agent.providers import (
    AvailableModel,
    ModelAttempt,
    ProviderChoice,
    ProviderInfo,
    ProviderStatus,
    check_provider,
    configured_providers,
    list_models,
    provider_info,
    route_providers,
    save_provider_settings,
)
from selfforge_agent.review.deepseek import DeepSeekReviewError, ProviderReviewer
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.runtime.workspace_diff import workspace_diff
from selfforge_agent.service import ReviewService
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner

app = FastAPI(title="selfForge Agent")
_STATIC = Path(__file__).resolve().parent / "static"
_WORKSPACES = Path(__file__).resolve().parents[3] / ".workspaces"
_CODING_JOBS = CodingJobManager()
_CODING_WORKSPACES = CodingWorkspaceManager()
app.mount("/assets", StaticFiles(directory=_STATIC), name="assets")


class ReviewRequest(BaseModel):
    base: str
    head: str


class GitHubReviewRequest(BaseModel):
    url: str
    provider: ProviderChoice = "rules"
    fallback: bool = True


class GitHubCheckoutRequest(BaseModel):
    url: str


class GitHubReviewResponse(BaseModel):
    pr: GitHubPR
    report: EvidenceReport
    ai_error: str | None = None
    model_provider: str | None = None
    model_attempts: list[ModelAttempt] = Field(default_factory=list)


class GitHubRepairRequest(BaseModel):
    url: str
    task: str
    trust_local_tests: bool = False
    sandbox: Literal["docker", "host"] = "docker"
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"
    fallback: bool = True


class GitHubRepairResponse(BaseModel):
    checkout: CheckoutResult
    report: RepairReport
    model_provider: str | None = None
    model_attempts: list[ModelAttempt] = Field(default_factory=list)


class GitHubGenerateTestRequest(GitHubRepairRequest):
    task: str = Field(min_length=1, max_length=8_000)
    timeout: float = Field(default=60, gt=0, le=300)


class GitHubGenerateTestResponse(BaseModel):
    checkout: CheckoutResult
    report: DifferentialTestReport


class GitHubGenerateTestsRequest(GitHubGenerateTestRequest):
    max_candidates: int = Field(default=2, ge=1, le=3)
    time_budget: float = Field(default=300, gt=0, le=600)
    target: RiskTarget | None = None


class GitHubGenerateTestsResponse(BaseModel):
    checkout: CheckoutResult
    batch: TestGenerationBatchReport


class AppConfig(BaseModel):
    providers: list[ProviderInfo]
    host_tests_enabled: bool


class CodingTaskRequest(BaseModel):
    repository: str = Field(min_length=1)
    task: str = Field(default="", max_length=8_000)
    resume_session: str | None = None
    workspace: Literal["worktree", "direct"] = "worktree"
    confirm_direct: bool = False
    sandbox: Literal["docker", "host"] = "docker"
    trust_local: bool = False
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"
    fallback: bool = True
    max_steps: int = Field(default=12, ge=1, le=100)
    time_budget: float = Field(default=300, gt=0, le=1_200)


class CodingJobResponse(BaseModel):
    job_id: str
    session_id: str
    state: str
    answer: str
    steps: int
    tools_used: int
    error: str
    cancel_requested: bool
    repository: str
    workspace_path: str
    branch: str
    base_sha: str
    can_resume: bool
    trace: list[dict]


class CodingWorkspaceRequest(BaseModel):
    repository: str = Field(min_length=1)


class CodingDiffFile(BaseModel):
    path: str
    status: str
    patch: str
    truncated: bool


class CodingDiffResponse(BaseModel):
    session_id: str
    branch: str
    base_sha: str
    files: list[CodingDiffFile]
    omitted_files: int


class ProviderCheckRequest(BaseModel):
    provider: Literal["all", "deepseek", "openrouter"] = "all"


class ProviderSettingsRequest(BaseModel):
    provider: Literal["deepseek", "openrouter"]
    base_url: str
    model: str
    api_key: str | None = None


def _require_local(client: Request) -> None:
    if client.client and client.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="This operation is available only from localhost")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=AppConfig)
def config() -> AppConfig:
    return AppConfig(
        providers=provider_info(),
        host_tests_enabled=os.environ.get("SELFFORGE_ENABLE_HOST_TESTS") == "1",
    )


def _coding_session_root(repository: Path) -> Path:
    fingerprint = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:20]
    return _WORKSPACES / "coding-sessions" / fingerprint


@app.post("/api/code/jobs", response_model=CodingJobResponse)
def start_coding_job(request: CodingTaskRequest, client: Request) -> dict:
    _require_local(client)
    repository = Path(request.repository).expanduser().resolve()
    if not repository.is_dir() or not (repository / ".git").exists():
        raise HTTPException(status_code=400, detail="Choose an existing Git repository root")
    if request.sandbox == "host" and (
        os.environ.get("SELFFORGE_ENABLE_HOST_TESTS") != "1" or not request.trust_local
    ):
        raise HTTPException(status_code=403, detail="Enable host tests and explicitly trust this repository")
    if request.workspace == "direct" and not request.confirm_direct:
        raise HTTPException(status_code=403, detail="Explicitly confirm direct edits to this repository")
    if not request.task.strip() and not request.resume_session:
        raise HTTPException(status_code=400, detail="A task or session ID is required")
    candidates = route_providers(request.provider, request.fallback)
    if not candidates:
        raise HTTPException(status_code=503, detail="A configured model provider is required")
    created_workspace = None
    job_started = False
    try:
        session_root = _CODING_WORKSPACES.session_root(
            repository, request.resume_session,
            legacy_roots=(_coding_session_root(repository), repository))
        session = SessionStore(session_root, request.resume_session)
        if request.resume_session:
            session.load()
        model = OpenAIChatAdapter(candidates)
        workspace = None
        if request.workspace == "worktree":
            workspace = (_CODING_WORKSPACES.get(repository, session.session_id)
                         if request.resume_session else
                         _CODING_WORKSPACES.create(repository, session.session_id))
            if not request.resume_session:
                created_workspace = workspace
        working_root = workspace.path if workspace else repository
        executor = (HostExecutor(working_root, trusted=True) if request.sandbox == "host"
                    else DockerExecutor(working_root))
        cancel = Event()
        agent = CodingAgent(model, ToolRegistry(working_root, executor),
                            session, max_steps=request.max_steps,
                            time_budget=request.time_budget, cancel=cancel)
        job = _CODING_JOBS.start(agent, request.task, resume=bool(request.resume_session),
                                 workspace_info=workspace)
        job_started = True
        return _CODING_JOBS.get(job.job_id)
    except (ValueError, OSError, WorktreeError) as exc:
        if created_workspace and not job_started:
            try:
                _CODING_WORKSPACES.remove(repository, created_workspace.session_id)
                git(repository, "branch", "-D", created_workspace.branch)
            except (ValueError, OSError, WorktreeError):
                # Keep a recoverable worktree if cleanup itself fails.
                pass
        raise HTTPException(status_code=409 if "already running" in str(exc) else 400,
                            detail=str(exc)) from exc


@app.post("/api/code/workspaces/{session_id}/remove")
def remove_coding_workspace(session_id: str, request: CodingWorkspaceRequest,
                            client: Request) -> dict[str, str]:
    _require_local(client)
    repository = Path(request.repository).expanduser().resolve()
    try:
        workspace = _CODING_WORKSPACES.get(repository, session_id)
        with _CODING_JOBS.workspace_guard(workspace.path):
            _CODING_WORKSPACES.remove(repository, session_id)
    except (ValueError, OSError, WorktreeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"removed": str(workspace.path), "branch": workspace.branch}


@app.get("/api/code/workspaces/{session_id}/diff", response_model=CodingDiffResponse)
def coding_workspace_diff(session_id: str, repository: str, client: Request) -> dict:
    _require_local(client)
    try:
        workspace = _CODING_WORKSPACES.get(Path(repository).expanduser().resolve(), session_id)
        return workspace_diff(workspace)
    except (ValueError, OSError, WorktreeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/code/jobs", response_model=list[CodingJobResponse])
def coding_jobs(repository: str, client: Request) -> list[dict]:
    _require_local(client)
    return _CODING_JOBS.list_jobs(Path(repository).expanduser().resolve())


@app.get("/api/code/jobs/{job_id}", response_model=CodingJobResponse)
def coding_job(job_id: str, client: Request) -> dict:
    _require_local(client)
    try:
        return _CODING_JOBS.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Coding job not found") from exc


@app.post("/api/code/jobs/{job_id}/cancel", response_model=CodingJobResponse)
def cancel_coding_job(job_id: str, client: Request) -> dict:
    _require_local(client)
    try:
        return _CODING_JOBS.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Coding job not found") from exc


@app.post("/api/providers/check", response_model=list[ProviderStatus])
def provider_check(request: ProviderCheckRequest, client: Request) -> list[ProviderStatus]:
    _require_local(client)
    return [
        check_provider(item) for item in configured_providers()
        if request.provider == "all" or request.provider == item.name
    ]


@app.post("/api/providers/config", response_model=ProviderInfo)
def provider_config(request: ProviderSettingsRequest, client: Request) -> ProviderInfo:
    _require_local(client)
    try:
        return save_provider_settings(request.provider, api_key=request.api_key,
                                      base_url=request.base_url, model=request.model)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/providers/{provider}/models", response_model=list[AvailableModel])
def provider_models(provider: Literal["deepseek", "openrouter"], client: Request) -> list[AvailableModel]:
    _require_local(client)
    item = next(entry for entry in configured_providers() if entry.name == provider)
    try:
        return list_models(item)
    except (ValueError, TypeError, ProviderHTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/github/review", response_model=GitHubReviewResponse)
def github_review(request: GitHubReviewRequest) -> GitHubReviewResponse:
    try:
        pr = load_pr(request.url)
    except GitHubPRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report = ReviewService().review_context(pr.context)
    ai_error = None
    selected = None
    attempts: list[ModelAttempt] = []
    if request.provider != "rules":
        candidates = route_providers(request.provider, request.fallback)
        if not candidates:
            ai_error = "No configured model provider is available for this selection"
        for item in candidates:
            try:
                findings, usage = ProviderReviewer(item).review_with_usage(pr.context)
            except DeepSeekReviewError as exc:
                attempts.append(ModelAttempt(
                    provider=item.name, model=item.model, status="error",
                    error=str(exc), usage=exc.usage,
                ))
                continue
            report.findings.extend(findings)
            attempts.append(ModelAttempt(
                provider=item.name, model=item.model, status="success", usage=usage
            ))
            selected = item.name
            break
        if candidates and selected is None:
            ai_error = "All configured model providers failed"
        if ai_error:
            report.errors.append(ai_error)
            report.execution_status = "error"
    return GitHubReviewResponse(
        pr=pr, report=report, ai_error=ai_error,
        model_provider=selected, model_attempts=attempts,
    )


@app.post("/api/github/checkout", response_model=CheckoutResult)
def github_checkout(request: GitHubCheckoutRequest, client: Request) -> CheckoutResult:
    if client.client and client.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Checkout is available only from localhost")
    try:
        return checkout_pr(request.url, _WORKSPACES)
    except GitHubPRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/github/repair", response_model=GitHubRepairResponse)
def github_repair(request: GitHubRepairRequest, client: Request) -> GitHubRepairResponse:
    if client.client and client.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Repair is available only from localhost")
    if (request.sandbox == "host" and
            (os.environ.get("SELFFORGE_ENABLE_HOST_TESTS") != "1" or not request.trust_local_tests)):
        raise HTTPException(status_code=403, detail="Enable host tests and explicitly trust this PR")
    candidates = route_providers(request.provider, request.fallback)
    if not candidates:
        raise HTTPException(status_code=503, detail="A configured model provider is required")
    try:
        pr = load_pr(request.url)
        checkout = checkout_pr(pr.url, _WORKSPACES)
        model = FallbackCodingModel(candidates)
        runner = TestRunner() if request.sandbox == "host" else DockerTestRunner()
        report = RepairAgent(model, runner=runner,
                             session=SessionStore(Path(checkout.path))).run(
            Path(checkout.path),
            checkout.diff_base_sha,
            pr.context.head_sha,
            request.task,
            trusted=request.trust_local_tests if request.sandbox == "host" else False,
        )
        return GitHubRepairResponse(
            checkout=checkout, report=report,
            model_provider=model.selected_provider, model_attempts=model.attempts,
        )
    except (GitHubPRError, GitContextError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/github/generate-test", response_model=GitHubGenerateTestResponse)
def github_generate_test(request: GitHubGenerateTestRequest, client: Request) -> GitHubGenerateTestResponse:
    pr, checkout, generator = _prepare_test_generation(request, client)
    try:
        report = generator.run(Path(checkout.path), checkout.diff_base_sha,
                               pr.context.head_sha, request.task,
                               trusted=request.trust_local_tests if request.sandbox == "host" else False,
                               timeout=request.timeout)
        return GitHubGenerateTestResponse(checkout=checkout, report=report)
    except (GitContextError, WorktreeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _prepare_test_generation(request: GitHubGenerateTestRequest, client: Request
                             ) -> tuple[GitHubPR, CheckoutResult, DifferentialTestGenerator]:
    _require_local(client)
    if (request.sandbox == "host" and
            (os.environ.get("SELFFORGE_ENABLE_HOST_TESTS") != "1" or not request.trust_local_tests)):
        raise HTTPException(status_code=403, detail="Enable host tests and explicitly trust this PR")
    candidates = route_providers(request.provider, request.fallback)
    if not candidates:
        raise HTTPException(status_code=503, detail="A configured model provider is required")
    try:
        pr = load_pr(request.url)
        if isinstance(request, GitHubGenerateTestsRequest):
            validate_risk_target(pr.context, request.target)
        checkout = checkout_pr(pr.url, _WORKSPACES)
        repository = Path(checkout.path)
        runner = TestRunner() if request.sandbox == "host" else DockerTestRunner()
        session = SessionStore(_CODING_WORKSPACES.session_root(repository))
        generator = DifferentialTestGenerator(
            OpenAIChatAdapter(candidates), runner=runner, session=session,
        )
        return pr, checkout, generator
    except (GitHubPRError, GitContextError, WorktreeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/github/generate-tests", response_model=GitHubGenerateTestsResponse)
def github_generate_tests(request: GitHubGenerateTestsRequest, client: Request
                          ) -> GitHubGenerateTestsResponse:
    pr, checkout, generator = _prepare_test_generation(request, client)
    try:
        batch = generator.run_many(
            Path(checkout.path), checkout.diff_base_sha, pr.context.head_sha, request.task,
            max_candidates=request.max_candidates, target=request.target,
            trusted=request.trust_local_tests if request.sandbox == "host" else False,
            timeout=request.timeout, time_budget=request.time_budget,
        )
        return GitHubGenerateTestsResponse(checkout=checkout, batch=batch)
    except (GitContextError, WorktreeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/review", response_model=EvidenceReport)
def review(request: ReviewRequest) -> EvidenceReport:
    configured = os.environ.get("SELFFORGE_REPO_ROOT")
    if not configured:
        raise HTTPException(status_code=503, detail="Repository root is not configured")
    try:
        return ReviewService().review(Path(configured), request.base, request.head)
    except GitContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
