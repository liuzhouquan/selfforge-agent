"""Test profile specification, loading, and immutable plan freezing."""

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from selfforge_agent.verification.harness.contracts import (
    TestExecutionRequest,
    TestPlan,
    compute_plan_digest,
)

MAX_PROFILE_BYTES = 512 * 1024  # 512 KiB


class TestProfile(BaseModel):
    """Declarative, versioned test profile definition."""
    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    name: str = Field(default="default", max_length=128)
    backend: str = Field(max_length=64)
    cwd: str = Field(default=".", max_length=4096)
    selection: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=60.0, gt=0, le=3600.0)
    sandbox: Literal["host", "docker"] = "docker"
    image: str = Field(default="selfforge-agent-sandbox:latest", max_length=256)
    env_whitelist: list[str] = Field(default_factory=list)
    profile_digest: str = Field(default="", max_length=64)

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError("timeout must be a finite positive number")
        if not math.isfinite(v) or v <= 0 or v > 3600:
            raise ValueError("timeout must be a finite positive number not exceeding 3600 seconds")
        return float(v)

    @model_validator(mode="after")
    def compute_digest(self) -> "TestProfile":
        if not self.profile_digest:
            payload = {
                "schema_version": self.schema_version,
                "name": self.name,
                "backend": self.backend,
                "cwd": self.cwd,
                "selection": self.selection,
                "timeout": self.timeout,
                "sandbox": self.sandbox,
                "image": self.image,
                "env_whitelist": sorted(self.env_whitelist),
            }
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            # Because model is frozen, use object.__setattr__
            object.__setattr__(self, "profile_digest", digest)
        return self


def load_test_profile(repository: Path, filename: str = "selfforge-test.json") -> TestProfile:
    """Safely load and parse a test profile from the repository root."""
    target = (repository / filename).resolve()
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError(f"Test profile not found: {filename}")

    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROFILE_BYTES:
                raise ValueError("Invalid test profile file")
            with open(fd, "rb", closefd=True) as handle:
                fd = -1
                content = handle.read(MAX_PROFILE_BYTES + 1)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError as exc:
        raise ValueError(f"Failed to read test profile: {exc}") from exc

    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"Malformed test profile JSON: {exc}") from exc

    return TestProfile(**data)


def build_frozen_plan_from_profile(
    request: TestExecutionRequest,
    profile: TestProfile,
) -> TestPlan:
    """Freeze a test plan combining request parameters and profile invariants."""
    repo = request.repository.resolve()
    effective_timeout = request.timeout if request.timeout != 60.0 else profile.timeout
    effective_cwd = request.cwd if request.cwd != "." else profile.cwd
    effective_sandbox = request.sandbox
    effective_image = request.image

    # Build command and report artifacts
    if profile.backend == "pytest":
        cmd = ["python", "-m", "pytest", "tests", "--junitxml={junit_xml}"]
        report_artifacts = ["junit.xml"]
        selection_summary = "tests"
    else:
        cmd = [profile.backend, "test"]
        report_artifacts = ["report.xml"]
        selection_summary = "default"

    plan_data = {
        "schema_version": "1.0",
        "adapter_id": profile.backend,
        "adapter_version": "1.0",
        "backend": profile.backend,
        "repository": str(repo),
        "cwd": effective_cwd,
        "command": cmd,
        "environment": f"{effective_sandbox}; profile={profile.name}; digest={profile.profile_digest}",
        "report_artifacts": report_artifacts,
        "selection_summary": selection_summary,
        "timeout": effective_timeout,
        "sandbox": effective_sandbox,
        "trusted": request.trusted,
        "image": effective_image,
    }
    digest = compute_plan_digest(plan_data)

    return TestPlan(
        schema_version="1.0",
        adapter_id=profile.backend,
        adapter_version="1.0",
        backend=profile.backend,
        repository=str(repo),
        cwd=effective_cwd,
        command=cmd,
        environment=f"{effective_sandbox}; profile={profile.name}; digest={profile.profile_digest}",
        report_artifacts=report_artifacts,
        selection_summary=selection_summary,
        timeout=effective_timeout,
        sandbox=effective_sandbox,
        trusted=request.trusted,
        image=effective_image,
        plan_digest=digest,
    )
