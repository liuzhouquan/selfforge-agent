"""Fail-closed validation for frozen evaluation policies and asset inventories."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from selfforge_agent.providers import validate_base_url
from selfforge_agent.runtime.benchmark import (
    AGENT_ERROR_CODE_TAXONOMY,
    FAILURE_REASON_TAXONOMY,
    MAX_REFERENCE_BYTES,
    BenchmarkSuite,
)

MAX_POLICY_BYTES = 512 * 1024
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}\Z")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}\Z")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})\Z")


def _normalize_sha256(value: str, *, field_name: str = "sha256") -> str:
    clean = value.strip()
    if not _SHA256_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a 64-character hex SHA-256 digest")
    return clean.lower()


def _normalize_commit_sha(value: str, *, field_name: str = "revision") -> str:
    clean = value.strip()
    if not _COMMIT_SHA_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a 40-character Git commit SHA")
    return clean.lower()


def _normalize_image_ref(value: str) -> str:
    clean = value.strip()
    if not clean or any(ch.isspace() for ch in clean):
        raise ValueError("Image reference must be a nonempty string without spaces")
    match = _IMAGE_DIGEST_RE.fullmatch(clean)
    if match:
        return f"sha256:{match.group(1).lower()}"
    return clean


def _read_regular_bytes(path: Path, *, max_bytes: int = MAX_POLICY_BYTES) -> bytes:
    """Read a regular non-symlink file with bounded size and non-blocking open."""
    temp_ignored = set(Path(tempfile.gettempdir()).parents)
    for ancestor in [path, *path.parents]:
        if ancestor not in temp_ignored and ancestor.is_symlink():
            raise ValueError(f"Refusing symlink path component: {ancestor}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"Refusing symlink file: {path}") from exc
        raise ValueError(f"Cannot read required file {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Expected regular file, got special file: {path}")
            data = handle.read(max_bytes + 1)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"Failed reading regular file {path}: {exc}") from exc
    if len(data) > max_bytes:
        raise ValueError(f"File exceeds size limit ({max_bytes} bytes): {path}")
    return data


def _parse_json_bytes(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"Malformed JSON in {label}: {exc}") from exc


def _resolve_relative(base_dir: Path, root_guard: Path, raw_rel: str) -> Path:
    rel = Path(raw_rel)
    if rel.is_absolute():
        raise ValueError(f"Policy path must be relative, got: {raw_rel}")
    unresolved = Path(os.path.abspath(base_dir / rel))
    temp_ignored = set(Path(tempfile.gettempdir()).parents)
    for ancestor in [unresolved, *unresolved.parents]:
        if ancestor not in temp_ignored and ancestor.is_symlink():
            raise ValueError(f"Refusing symlink path component: {ancestor}")
    candidate = unresolved.resolve()
    guard = root_guard.resolve()
    if not candidate.is_relative_to(guard):
        raise ValueError(f"Policy path escapes allowed root {guard}: {raw_rel}")
    return candidate


class HistoricalBaselineRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: str = Field(min_length=1)
    manifest_sha256: str
    assets_inventory: str = Field(min_length=1)
    assets_inventory_sha256: str
    runs: int = Field(ge=1)
    successes: int = Field(ge=0)
    separate_from_new_policy: Literal[True]

    @field_validator("manifest_sha256", "assets_inventory_sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)


class PinnedRepoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    revision: str

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        clean = value.strip()
        if not clean.startswith("https://"):
            raise ValueError(f"Pinned repository URL must use HTTPS: {value}")
        return clean

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, value: str) -> str:
        return _normalize_commit_sha(value)


class ImageProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str
    dockerfile: str | None = None
    lockfile: str | None = None
    lockfile_sha256: str | None = None
    cases: int = Field(ge=1)
    historical_digest_distinct_from: str

    @field_validator("reference", "historical_digest_distinct_from")
    @classmethod
    def _check_image_ref(cls, value: str) -> str:
        return _normalize_image_ref(value)

    @field_validator("lockfile_sha256")
    @classmethod
    def _check_lock_sha256(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_sha256(value, field_name="lockfile_sha256")


class LockfileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        return _normalize_sha256(value, field_name="lockfile sha256")


class EnvironmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox: Literal["docker"]
    network_mode: Literal["none"]
    image_policy: Literal["resolve_fresh_local_build"]
    images: dict[str, ImageProfileSpec] = Field(min_length=1)
    lockfiles: dict[str, LockfileRef] = Field(default_factory=dict)


class ProviderPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str
    fallback: bool
    require_effective_config_check: Literal[True]

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return validate_base_url(value)


class BudgetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repeats: int = Field(ge=1, le=20)
    total_planned_runs: int = Field(ge=1)
    max_steps: int = Field(ge=1, le=100)
    time_budget_seconds: float = Field(gt=0, allow_inf_nan=False)
    test_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    context_budget_tokens: int = Field(ge=1000)


class CeilingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_case_attempts: int = Field(ge=1)
    max_model_calls_upper_bound: int = Field(ge=1)
    max_wall_clock_minutes: int = Field(ge=1)
    max_spend_usd: float = Field(gt=0, allow_inf_nan=False)
    require_rate_card_for_spend_estimate: Literal[True]
    allow_auto_rerun: Literal[False]


class FailureTaxonomySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success_reason: Literal["passed"]
    failure_reasons: list[str] = Field(min_length=1)
    agent_error_codes: list[str] = Field(min_length=1)

    @field_validator("failure_reasons")
    @classmethod
    def _check_failure_reasons(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Duplicate entries in failure_reasons")
        missing = set(FAILURE_REASON_TAXONOMY) - set(values)
        if missing:
            raise ValueError(f"failure_reasons is missing canonical reasons: {sorted(missing)}")
        extra = set(values) - set(FAILURE_REASON_TAXONOMY)
        if extra:
            raise ValueError(f"failure_reasons has unknown entries: {sorted(extra)}")
        return values

    @field_validator("agent_error_codes")
    @classmethod
    def _check_agent_error_codes(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Duplicate entries in agent_error_codes")
        missing = set(AGENT_ERROR_CODE_TAXONOMY) - set(values)
        if missing:
            raise ValueError(f"agent_error_codes is missing canonical codes: {sorted(missing)}")
        return values


class ExecutionStatusSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_benchmark_executed: bool
    completed_runs: int = Field(ge=0)
    successes: int | None = Field(default=None, ge=0)
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    score_status: Literal["pending_live_execution", "completed"]

    @model_validator(mode="after")
    def _check_consistency(self) -> ExecutionStatusSpec:
        if not self.model_benchmark_executed:
            if (
                self.score_status != "pending_live_execution"
                or self.completed_runs != 0
                or self.successes is not None
                or self.success_rate is not None
            ):
                raise ValueError(
                    "Unexecuted policy must remain pending with completed_runs=0 "
                    "and null successes/success_rate"
                )
        elif self.completed_runs == 0 or self.successes is None or self.success_rate is None:
            raise ValueError(
                "Executed policy must have completed_runs > 0 and concrete scores"
            )
        if self.model_benchmark_executed:
            if self.score_status != "completed" or self.successes > self.completed_runs:
                raise ValueError("Executed policy has inconsistent status or success count")
            if not math.isclose(self.success_rate, self.successes / self.completed_runs,
                                rel_tol=0, abs_tol=1e-12):
                raise ValueError("Executed policy success_rate differs from observed counts")
        return self


class EvalPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal[3]
    suite_id: str = Field(min_length=1, max_length=100)
    manifest: str = Field(min_length=1)
    manifest_sha256: str
    assets_inventory: str = Field(min_length=1)
    assets_inventory_sha256: str
    historical_baseline: HistoricalBaselineRef
    repositories: dict[str, PinnedRepoSpec] = Field(min_length=1)
    environment: EnvironmentSpec
    provider_policy: ProviderPolicySpec
    budgets: BudgetSpec
    ceilings: CeilingSpec
    failure_taxonomy: FailureTaxonomySpec
    execution_status: ExecutionStatusSpec

    @field_validator("manifest_sha256", "assets_inventory_sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)


@dataclass(frozen=True)
class VerifiedEvalPolicyBundle:
    policy: EvalPolicySpec
    suite: BenchmarkSuite
    assets: dict[str, str]
    manifest_sha256: str
    assets_inventory_sha256: str
    manifest_path: Path
    policy_path: Path
    policy_sha256: str


def _project_root_for(policy_dir: Path) -> Path:
    for parent in [policy_dir, *policy_dir.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return policy_dir


def verify_eval_policy_bundle(policy_path: Path) -> VerifiedEvalPolicyBundle:
    """Load and verify a frozen evaluation policy, manifest, assets, and historical hashes."""
    # Check the spelling supplied by the caller before resolve erases symlink ancestors.
    policy_bytes = _read_regular_bytes(policy_path)
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    resolved_policy = policy_path.resolve()
    policy_dir = resolved_policy.parent
    project_root = _project_root_for(policy_dir)

    raw_policy = _parse_json_bytes(policy_bytes, label="policy")
    policy = EvalPolicySpec.model_validate(raw_policy)

    manifest_path = _resolve_relative(policy_dir, project_root, policy.manifest)
    manifest_bytes = _read_regular_bytes(manifest_path)
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha256 != policy.manifest_sha256:
        raise ValueError(
            f"Manifest SHA-256 mismatch: expected {policy.manifest_sha256}, "
            f"got {actual_manifest_sha256}"
        )
    suite = BenchmarkSuite.model_validate(_parse_json_bytes(manifest_bytes, label="manifest"))

    if len({case.id for case in suite.cases}) != len(suite.cases):
        raise ValueError("Manifest contains duplicate case IDs")

    assets_path = _resolve_relative(policy_dir, project_root, policy.assets_inventory)
    assets_bytes = _read_regular_bytes(assets_path)
    actual_assets_sha256 = hashlib.sha256(assets_bytes).hexdigest()
    if actual_assets_sha256 != policy.assets_inventory_sha256:
        raise ValueError(
            f"Assets inventory SHA-256 mismatch: expected {policy.assets_inventory_sha256}, "
            f"got {actual_assets_sha256}"
        )
    raw_assets = _parse_json_bytes(assets_bytes, label="assets inventory")
    if not isinstance(raw_assets, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_assets.items()
    ):
        raise ValueError("Assets inventory must map relative path strings to SHA-256 hex strings")
    assets = {k: _normalize_sha256(v, field_name=f"asset digest ({k})") for k, v in raw_assets.items()}

    # Verify historical baseline remains intact and strictly distinct from new policy.
    hist = policy.historical_baseline
    hist_manifest_path = _resolve_relative(policy_dir, project_root, hist.manifest)
    hist_manifest_sha = hashlib.sha256(_read_regular_bytes(hist_manifest_path)).hexdigest()
    if hist_manifest_sha != hist.manifest_sha256:
        raise ValueError(
            f"Historical manifest SHA-256 mismatch: expected {hist.manifest_sha256}, "
            f"got {hist_manifest_sha}"
        )
    if hist_manifest_sha == actual_manifest_sha256:
        raise ValueError("New policy manifest SHA-256 must differ from historical baseline")

    hist_assets_path = _resolve_relative(policy_dir, project_root, hist.assets_inventory)
    hist_assets_sha = hashlib.sha256(_read_regular_bytes(hist_assets_path)).hexdigest()
    if hist_assets_sha != hist.assets_inventory_sha256:
        raise ValueError(
            f"Historical assets SHA-256 mismatch: expected {hist.assets_inventory_sha256}, "
            f"got {hist_assets_sha}"
        )

    manifest_dir = manifest_path.parent
    expected_asset_keys: set[str] = set()
    image_refs = {spec.reference for spec in policy.environment.images.values()}
    historical_digests = {spec.historical_digest_distinct_from for spec in policy.environment.images.values()}
    image_counts: dict[str, int] = {ref: 0 for ref in image_refs}

    for case in suite.cases:
        repo_key = Path(case.repository).name
        if repo_key not in policy.repositories:
            raise ValueError(f"Case {case.id} repository {repo_key!r} is absent from policy")
        if _normalize_commit_sha(case.revision) != policy.repositories[repo_key].revision:
            raise ValueError(f"Case {case.id} revision differs from pinned policy revision")
        if not case.reference_patch:
            raise ValueError(f"Case {case.id} must define reference_patch for preflight")
        if not case.image:
            raise ValueError(f"Case {case.id} must explicitly set an environment image reference")
        normalized_case_img = _normalize_image_ref(case.image)
        if normalized_case_img in historical_digests:
            raise ValueError(f"Case {case.id} reuses historical frozen image digest")
        if normalized_case_img not in image_refs:
            raise ValueError(f"Case {case.id} image {case.image!r} is undeclared in policy")
        image_counts[normalized_case_img] += 1

        if not all(not Path(p).is_absolute() and ".." not in Path(p).parts for p in case.allowed_changes):
            raise ValueError(f"Case {case.id} has unsafe allowed_changes path")

        holdout_resolved = _resolve_relative(manifest_dir, manifest_dir, case.holdout_test)
        repo_resolved = (manifest_dir / case.repository).resolve()
        if holdout_resolved.is_relative_to(repo_resolved):
            raise ValueError(f"Case {case.id} holdout_test must stay outside target repository")

        for field_name in ("holdout_test", "reference_patch"):
            rel = getattr(case, field_name)
            expected_asset_keys.add(rel)
            asset_resolved = _resolve_relative(manifest_dir, manifest_dir, rel)
            actual_digest = hashlib.sha256(
                _read_regular_bytes(asset_resolved, max_bytes=MAX_REFERENCE_BYTES)
            ).hexdigest()
            if rel not in assets or assets[rel] != actual_digest:
                raise ValueError(f"Asset SHA-256 mismatch or missing entry for {rel}")

    if set(assets) != expected_asset_keys:
        raise ValueError("Assets inventory contains extra unreferenced asset keys")

    for profile_spec in policy.environment.images.values():
        if image_counts[profile_spec.reference] != profile_spec.cases:
            raise ValueError(
                f"Image profile {profile_spec.reference} expects {profile_spec.cases} cases, "
                f"found {image_counts[profile_spec.reference]}"
            )

    for profile_spec in policy.environment.images.values():
        if (profile_spec.lockfile is None) != (profile_spec.lockfile_sha256 is None):
            raise ValueError("Image profile lockfile and lockfile_sha256 must be supplied together")
        if profile_spec.lockfile is not None:
            lock_path = _resolve_relative(policy_dir, project_root, profile_spec.lockfile)
            actual_lock_sha = hashlib.sha256(_read_regular_bytes(lock_path)).hexdigest()
            if actual_lock_sha != profile_spec.lockfile_sha256:
                raise ValueError(f"Image profile lockfile SHA-256 mismatch for {profile_spec.lockfile}")

    for lock_ref in policy.environment.lockfiles.values():
        lock_path = _resolve_relative(policy_dir, project_root, lock_ref.path)
        actual_lock_sha = hashlib.sha256(_read_regular_bytes(lock_path)).hexdigest()
        if actual_lock_sha != lock_ref.sha256:
            raise ValueError(
                f"Lockfile SHA-256 mismatch for {lock_ref.path}: "
                f"expected {lock_ref.sha256}, got {actual_lock_sha}"
            )

    if policy.budgets.total_planned_runs != len(suite.cases) * policy.budgets.repeats:
        raise ValueError("budgets.total_planned_runs must equal len(cases) * repeats")
    if policy.ceilings.max_case_attempts != policy.budgets.total_planned_runs:
        raise ValueError("ceilings.max_case_attempts must equal budgets.total_planned_runs")
    if policy.execution_status.completed_runs > policy.budgets.total_planned_runs:
        raise ValueError("Execution completed_runs exceeds planned attempts")
    expected_max_calls = policy.budgets.total_planned_runs * policy.budgets.max_steps
    if policy.ceilings.max_model_calls_upper_bound != expected_max_calls:
        raise ValueError("ceilings.max_model_calls_upper_bound must equal planned_runs * max_steps")

    return VerifiedEvalPolicyBundle(
        policy=policy,
        suite=suite,
        assets=assets,
        manifest_sha256=actual_manifest_sha256,
        assets_inventory_sha256=actual_assets_sha256,
        manifest_path=manifest_path,
        policy_path=resolved_policy,
        policy_sha256=policy_sha256,
    )


def make_policy_provenance(bundle: VerifiedEvalPolicyBundle) -> dict[str, Any]:
    """Extract standard provenance metadata from a verified policy bundle."""
    return {
        "policy_path": str(bundle.policy_path),
        "policy_sha256": bundle.policy_sha256,
        "policy_version": bundle.policy.policy_version,
        "suite_id": bundle.policy.suite_id,
        "manifest_sha256": bundle.manifest_sha256,
        "assets_inventory_sha256": bundle.assets_inventory_sha256,
    }


def enforce_policy_cli_args(bundle: VerifiedEvalPolicyBundle, args: Any) -> None:
    """Enforce that CLI flags and parameters do not conflict with the verified policy."""
    pol = bundle.policy
    if getattr(args, "sandbox", None) is not None and args.sandbox != pol.environment.sandbox:
        raise ValueError(
            f"CLI sandbox '{args.sandbox}' conflicts with policy sandbox '{pol.environment.sandbox}'"
        )
    if getattr(args, "image", None) is not None:
        allowed_images = {_normalize_image_ref(spec.reference) for spec in pol.environment.images.values()}
        if _normalize_image_ref(args.image) not in allowed_images:
            raise ValueError(
                f"CLI image '{args.image}' is not declared in evaluation policy ({sorted(allowed_images)})"
            )
    if getattr(args, "repeats", None) is not None and args.repeats != pol.budgets.repeats:
        raise ValueError(
            f"CLI repeats {args.repeats} conflicts with policy requirement {pol.budgets.repeats}"
        )
    if getattr(args, "max_steps", None) is not None and args.max_steps != pol.budgets.max_steps:
        raise ValueError(
            f"CLI max-steps {args.max_steps} conflicts with policy requirement {pol.budgets.max_steps}"
        )
    if getattr(args, "time_budget", None) is not None and not math.isclose(
        float(args.time_budget), float(pol.budgets.time_budget_seconds), abs_tol=1e-3
    ):
        raise ValueError(
            f"CLI time-budget {args.time_budget} conflicts with policy requirement {pol.budgets.time_budget_seconds}"
        )
    if getattr(args, "test_timeout", None) is not None and not math.isclose(
        float(args.test_timeout), float(pol.budgets.test_timeout_seconds), abs_tol=1e-3
    ):
        raise ValueError(
            f"CLI test-timeout {args.test_timeout} conflicts with policy requirement {pol.budgets.test_timeout_seconds}"
        )
    prov_policy = pol.provider_policy
    if getattr(args, "provider", None) is not None and args.provider != prov_policy.provider:
        raise ValueError(
            f"CLI provider '{args.provider}' conflicts with policy requirement '{prov_policy.provider}'"
        )
    if getattr(args, "model", None) is not None and args.model != prov_policy.model:
        raise ValueError(
            f"CLI model '{args.model}' conflicts with policy requirement '{prov_policy.model}'"
        )
    if getattr(args, "base_url", None) is not None and args.base_url.rstrip("/") != prov_policy.base_url.rstrip("/"):
        raise ValueError(
            f"CLI base-url '{args.base_url}' conflicts with policy requirement '{prov_policy.base_url}'"
        )
