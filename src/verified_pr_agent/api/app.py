"""Local web workbench and constrained review API."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from verified_pr_agent.coding.agent import RepairAgent
from verified_pr_agent.coding.model_client import OpenAICompatibleCodingModel
from verified_pr_agent.coding.models import RepairReport
from verified_pr_agent.core.git_context import GitContextError
from verified_pr_agent.integrations.github_pr import (
    CheckoutResult,
    GitHubPR,
    GitHubPRError,
    checkout_pr,
    load_pr,
)
from verified_pr_agent.models import EvidenceReport
from verified_pr_agent.review.deepseek import DeepSeekReviewer, DeepSeekReviewError
from verified_pr_agent.service import ReviewService

app = FastAPI(title="Verified PR Agent")
_STATIC = Path(__file__).resolve().parent / "static"
_WORKSPACES = Path(__file__).resolve().parents[3] / ".workspaces"
app.mount("/assets", StaticFiles(directory=_STATIC), name="assets")


class ReviewRequest(BaseModel):
    base: str
    head: str


class GitHubReviewRequest(BaseModel):
    url: str
    use_deepseek: bool = False


class GitHubCheckoutRequest(BaseModel):
    url: str


class GitHubReviewResponse(BaseModel):
    pr: GitHubPR
    report: EvidenceReport
    ai_error: str | None = None


class GitHubRepairRequest(BaseModel):
    url: str
    task: str
    trust_local_tests: bool = False


class GitHubRepairResponse(BaseModel):
    checkout: CheckoutResult
    report: RepairReport


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, bool]:
    return {
        "deepseek_available": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "host_tests_enabled": os.environ.get("VPA_ENABLE_HOST_TESTS") == "1",
    }


@app.post("/api/github/review", response_model=GitHubReviewResponse)
def github_review(request: GitHubReviewRequest) -> GitHubReviewResponse:
    try:
        pr = load_pr(request.url)
    except GitHubPRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report = ReviewService().review_context(pr.context)
    ai_error = None
    if request.use_deepseek:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            ai_error = "DEEPSEEK_API_KEY is not configured"
        else:
            try:
                report.findings.extend(DeepSeekReviewer(key).review(pr.context))
            except DeepSeekReviewError as exc:
                ai_error = str(exc)
        if ai_error:
            report.errors.append(ai_error)
            report.execution_status = "error"
    return GitHubReviewResponse(pr=pr, report=report, ai_error=ai_error)


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
    if os.environ.get("VPA_ENABLE_HOST_TESTS") != "1" or not request.trust_local_tests:
        raise HTTPException(status_code=403, detail="Enable host tests and explicitly trust this PR")
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("VPA_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="A model API key is required for repair")
    try:
        pr = load_pr(request.url)
        checkout = checkout_pr(pr.url, _WORKSPACES)
        model = OpenAICompatibleCodingModel(
            model=os.environ.get("VPA_MODEL") or "deepseek-flash",
            base_url=os.environ.get("VPA_API_BASE_URL") or "https://api.deepseek.com",
            api_key=key,
        )
        report = RepairAgent(model).run(
            Path(checkout.path),
            checkout.diff_base_sha,
            pr.context.head_sha,
            request.task,
            trusted=True,
        )
        return GitHubRepairResponse(checkout=checkout, report=report)
    except (GitHubPRError, GitContextError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/review", response_model=EvidenceReport)
def review(request: ReviewRequest) -> EvidenceReport:
    configured = os.environ.get("VERIFIED_PR_AGENT_REPO_ROOT")
    if not configured:
        raise HTTPException(status_code=503, detail="Repository root is not configured")
    try:
        return ReviewService().review(Path(configured), request.base, request.head)
    except GitContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
