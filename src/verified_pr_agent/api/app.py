"""API restricted to the single repository configured by the server operator."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from verified_pr_agent.core.git_context import GitContextError
from verified_pr_agent.models import EvidenceReport
from verified_pr_agent.service import ReviewService

app = FastAPI(title="Verified PR Agent")


class ReviewRequest(BaseModel):
    base: str
    head: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/review", response_model=EvidenceReport)
def review(request: ReviewRequest) -> EvidenceReport:
    configured = os.environ.get("VERIFIED_PR_AGENT_REPO_ROOT")
    if not configured:
        raise HTTPException(status_code=503, detail="Repository root is not configured")
    try:
        return ReviewService().review(Path(configured), request.base, request.head)
    except GitContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
