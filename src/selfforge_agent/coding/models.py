"""Evidence and the narrow edit contract for a bounded coding agent."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from selfforge_agent.models import Finding, PRContext, TestResult


class SourceFile(BaseModel):
    path: str
    content: str


class CodeEdit(BaseModel):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class RepairObservation(BaseModel):
    task: str
    attempt: int
    files: list[SourceFile]
    findings: list[Finding] = Field(default_factory=list)
    failing_test_output: str
    feedback: str = ""


class RepairAttempt(BaseModel):
    number: int
    edit: CodeEdit | None = None
    test_result: TestResult | None = None
    patch: str = ""
    error: str | None = None


class RepairReport(BaseModel):
    run_id: str
    repository: str
    base_sha: str
    head_sha: str
    task: str
    context: PRContext
    findings: list[Finding]
    status: Literal["verified", "unresolved", "inconclusive", "error"]
    base_test: TestResult | None = None
    head_test: TestResult | None = None
    attempts: list[RepairAttempt] = Field(default_factory=list)
    verified_patch: str = ""
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
