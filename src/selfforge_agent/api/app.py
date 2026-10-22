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
from selfforge_agent.service import ReviewService
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner

app = FastAPI(title="selfForge Agent")
_STATIC = Path(__file__).resolve().parent / "static"
_WORKSPACES = Path(__file__).resolve().parents[3] / ".workspaces"
_CODING_JOBS = CodingJobManager()
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


class AppConfig(BaseModel):
    providers: list[ProviderInfo]
    host_tests_enabled: bool


class CodingTaskRequest(BaseModel):
    repository: str = Field(min_length=1)
    task: str = Field(default="", max_length=8_000)
    resume_session: str | None = None
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
    trace: list[dict]


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
    if not request.task.strip() and not request.resume_session:
        raise HTTPException(status_code=400, detail="A task or session ID is required")
    candidates = route_providers(request.provider, request.fallback)
    if not candidates:
        raise HTTPException(status_code=503, detail="A configured model provider is required")
    try:
        session = SessionStore(_coding_session_root(repository), request.resume_session)
        if request.resume_session:
            session.load()
        executor = (HostExecutor(repository, trusted=True) if request.sandbox == "host"
                    else DockerExecutor(repository))
        cancel = Event()
        agent = CodingAgent(OpenAIChatAdapter(candidates), ToolRegistry(repository, executor),
                            session, max_steps=request.max_steps,
                            time_budget=request.time_budget, cancel=cancel)
        job = _CODING_JOBS.start(agent, request.task, resume=bool(request.resume_session))
        return _CODING_JOBS.get(job.job_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409 if "already running" in str(exc) else 400,
                            detail=str(exc)) from exc


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


@app.post("/review", response_model=EvidenceReport)
def review(request: ReviewRequest) -> EvidenceReport:
    configured = os.environ.get("SELFFORGE_REPO_ROOT")
    if not configured:
        raise HTTPException(status_code=503, detail="Repository root is not configured")
    try:
        return ReviewService().review(Path(configured), request.base, request.head)
    except GitContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
