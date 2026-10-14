"""Validated data exchanged between the Git, review, test and report layers."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ChangeKind(str, Enum):
    added = "added"
    removed = "removed"
    context = "context"


class DiffLine(BaseModel):
    kind: ChangeKind
    content: str
    old_line: int | None = Field(default=None, ge=1)
    new_line: int | None = Field(default=None, ge=1)


class DiffHunk(BaseModel):
    header: str
    old_start: int = Field(ge=0)
    new_start: int = Field(ge=0)
    lines: list[DiffLine]


class ChangedFile(BaseModel):
    path: str
    previous_path: str | None = None
    status: str
    hunks: list[DiffHunk]
    binary: bool = False


class PRContext(BaseModel):
    repository: str
    base_sha: str
    head_sha: str
    files: list[ChangedFile]


class VerificationStatus(str, Enum):
    suspected = "suspected"
    reproduced = "reproduced"
    unverified = "unverified"
    refuted = "refuted"
    error = "error"


class Finding(BaseModel):
    file_path: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    severity: str
    category: str
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    verification_status: VerificationStatus
    source: str = "heuristic"


class TestResult(BaseModel):
    command: list[str]
    environment: str
    exit_code: int | None
    duration_seconds: float = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)
    status: str
    stdout: str
    stderr: str
    error_message: str | None = None


class EvidenceReport(BaseModel):
    run_id: str
    repository: str
    base_sha: str
    head_sha: str
    context: PRContext
    findings: list[Finding]
    test_results: list[TestResult]
    execution_status: str
    started_at: datetime
    finished_at: datetime
    errors: list[str]
