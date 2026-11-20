"""Shared test harness contracts for language-agnostic test execution.

Defines versioned requests, frozen execution plans, normalized test results,
and shared error classifications.
"""

import hashlib
import json
import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from selfforge_agent.models import TestFailure, TestResult

PROTECTED_TARGETS = {
    ".git", ".venv", ".env", ".local", ".selfforge", ".workspaces", ".aws", ".ssh",
    ".npmrc", ".pypirc", ".netrc",
}

_IMAGE_PATTERN = re.compile(r"[a-zA-Z0-9_.:/-]+(?:@sha256:[a-fA-F0-9]{64})?\Z")


def is_protected_name(name: str) -> bool:
    """Return True if path segment is a protected environment or configuration file/dir."""
    return name in PROTECTED_TARGETS or name.startswith(".env.")


class UnsupportedBackendError(ValueError):
    """Raised when an unsupported or unregistered test backend is requested."""


class HarnessStatus(str, Enum):
    """Normalized overall execution status."""
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class HarnessReasonCode(str, Enum):
    """Controlled, machine-consumable categorization of harness outcomes."""
    TEST_FAILURE = "test_failure"
    BUILD_ERROR = "build_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INVALID_EVIDENCE = "invalid_evidence"
    UNSUPPORTED_BACKEND = "unsupported_backend"
    AMBIGUOUS_SELECTION = "ambiguous_selection"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    EMPTY_SUITE = "empty_suite"
    ALL_SKIPPED = "all_skipped"
    COUNT_MISMATCH = "count_mismatch"


class PytestSelection(BaseModel):
    """Framework-specific test selection parameters for pytest."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    test_path: str = Field(default="tests", max_length=4096)
    keyword: str | None = Field(default=None, max_length=512)

    @field_validator("test_path")
    @classmethod
    def validate_test_path(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("test_path must not be blank")
        if "\x00" in v:
            raise ValueError("test_path must not contain null bytes")
        v = v.strip()
        p = Path(v)
        if p.is_absolute():
            raise ValueError("test_path must be a relative path inside the repository")
        for part in p.parts:
            if part == ".." or is_protected_name(part):
                raise ValueError(f"test_path traverses protected or invalid path component: {part}")
        return v

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("keyword must not be blank")
        v = v.strip()
        if v.startswith("-"):
            raise ValueError("keyword must not start with '-'")
        if "\x00" in v:
            raise ValueError("keyword must not contain null bytes")
        if any(ord(c) < 32 and c not in " \t" for c in v) or "\n" in v or "\r" in v:
            raise ValueError("keyword contains control characters or newlines")
        return v


class TestExecutionRequest(BaseModel):
    """Versioned test execution request specifying repository, target, budget and backend."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    repository: Path
    backend: str = Field(default="pytest", max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    cwd: str = Field(default=".", max_length=4096)
    selection: PytestSelection | dict[str, Any] | None = None
    timeout: float = Field(default=60.0, gt=0, le=3600.0)
    sandbox: Literal["host", "docker"] = "docker"
    trusted: bool = False
    image: str = Field(default="selfforge-agent-sandbox:latest", max_length=256)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, v: Path) -> Path:
        s = str(v)
        if "\x00" in s:
            raise ValueError("repository must not contain null bytes")
        if not v.exists() or not v.is_dir():
            raise ValueError("repository must be an existing directory")
        resolved = v.resolve()
        if not resolved.is_dir():
            raise ValueError("repository must be an existing directory")
        return resolved

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("cwd must not be blank")
        if "\x00" in v:
            raise ValueError("cwd must not contain null bytes")
        v = v.strip()
        p = Path(v)
        if p.is_absolute():
            raise ValueError("cwd must be a relative path inside repository")
        for part in p.parts:
            if part == ".." or is_protected_name(part):
                raise ValueError(f"cwd traverses protected or invalid path component: {part}")
        return v

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError("timeout must be a finite positive number")
        if not math.isfinite(v) or v <= 0 or v > 3600:
            raise ValueError("timeout must be a finite positive number not exceeding 3600 seconds")
        return float(v)

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("image must not be blank")
        v = v.strip()
        if not _IMAGE_PATTERN.fullmatch(v) or len(v) > 256 or "\x00" in v or v.startswith("-"):
            raise ValueError(f"Invalid Docker image name: {v!r}")
        return v


def compute_plan_digest(data: dict[str, Any]) -> str:
    """Deterministically compute SHA-256 hex digest of a plan payload."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TestPlan(BaseModel):
    """Frozen, immutable execution plan with deterministic plan digest."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    adapter_id: str = Field(max_length=64)
    adapter_version: str = Field(default="1.0", max_length=32)
    backend: str = Field(max_length=64)
    repository: str = Field(max_length=4096)
    cwd: str = Field(default=".", max_length=4096)
    command: list[str]
    environment: str = Field(max_length=1024)
    report_artifacts: list[str] = Field(default_factory=list)
    selection_summary: str = Field(max_length=1024)
    timeout: float = Field(gt=0, le=3600.0)
    sandbox: Literal["host", "docker"]
    trusted: bool = False
    image: str = Field(default="selfforge-agent-sandbox:latest", max_length=256)
    plan_digest: str = Field(min_length=64, max_length=64)


class TestCaseResult(BaseModel):
    """Normalized individual testcase outcome."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=512)
    classname: str | None = Field(default=None, max_length=512)
    status: Literal["passed", "failed", "error", "skipped"]
    duration_seconds: float = Field(ge=0.0)
    message: str | None = Field(default=None, max_length=1024)
    detail: str | None = Field(default=None, max_length=4096)

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def validate_duration(cls, v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError("duration_seconds must be a finite non-negative number")
        if not math.isfinite(v) or v < 0:
            raise ValueError("duration_seconds must be a finite non-negative number")
        return float(v)


class TestRunResult(BaseModel):
    """Normalized overall execution result complying with shared test harness contract."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    run_id: str = Field(max_length=64)
    adapter_id: str = Field(max_length=64)
    adapter_version: str = Field(default="1.0", max_length=32)
    plan_digest: str = Field(min_length=64, max_length=64)
    status: HarnessStatus
    reason_code: HarnessReasonCode | None = None
    exit_code: int | None = None
    duration_seconds: float = Field(ge=0.0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    test_cases: list[TestCaseResult] = Field(default_factory=list)
    failure_details: list[TestFailure] = Field(default_factory=list, max_length=5)
    failure_details_truncated: bool = False
    stdout: str = Field(default="", max_length=50000)
    stderr: str = Field(default="", max_length=50000)
    error_message: str | None = None
    environment: str = Field(max_length=1024)
    command: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Shared success criteria:

        1. Status must be PASSED with no reason_code.
        2. At least one test case must actually pass (passed > 0).
        3. Zero failures and zero errors.
        4. Exit code must be 0 (or None when status is explicitly PASSED).
        5. Total must match passed + skipped.
        """
        if self.status != HarnessStatus.PASSED:
            return False
        if self.reason_code is not None:
            return False
        if self.passed <= 0 or self.failed > 0 or self.errors > 0:
            return False
        if self.exit_code not in (0, None):
            return False
        return self.total == self.passed + self.skipped

    def to_test_result(self) -> TestResult:
        """Convert to legacy TestResult for existing consumers."""
        return TestResult(
            command=self.command,
            environment=self.environment,
            exit_code=self.exit_code,
            duration_seconds=self.duration_seconds,
            passed=self.passed,
            failed=self.failed,
            errors=self.errors,
            skipped=self.skipped,
            status=self.status.value,
            stdout=self.stdout[-20000:],
            stderr=self.stderr[-20000:],
            error_message=self.error_message,
            failure_details=self.failure_details[:5],
            failure_details_truncated=self.failure_details_truncated,
        )
