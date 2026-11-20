"""Tests for SF-HARNESS-02: Frozen test plans, multi-language discovery, and environment staging."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.runtime.execution import _stage_workspace
from selfforge_agent.runtime.fingerprint import workspace_fingerprint
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
    TestExecutionRequest,
)
from selfforge_agent.verification.harness.discovery import (
    AmbiguousSelectionError,
    discover_project_candidates,
    resolve_project_candidate,
)
from selfforge_agent.verification.harness.profile import (
    TestProfile,
    build_frozen_plan_from_profile,
    load_test_profile,
)
from selfforge_agent.verification.harness.service import TestHarness

# ============================================================================
# SF-HARNESS-02-A1: Read-only candidate discovery & monorepo ambiguity handling
# ============================================================================

def test_discover_pytest_candidate(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    candidates = discover_project_candidates(tmp_path)
    assert len(candidates) >= 1
    pytest_cand = next((c for c in candidates if c.backend == "pytest"), None)
    assert pytest_cand is not None
    assert pytest_cand.cwd == "."
    assert pytest_cand.manifest_file == "pyproject.toml"
    assert len(pytest_cand.manifest_digest) == 64


def test_discover_vitest_candidate(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "devDependencies": {"vitest": "^1.0.0"}}', encoding="utf-8"
    )
    candidates = discover_project_candidates(tmp_path)
    vitest_cand = next((c for c in candidates if c.backend == "vitest"), None)
    assert vitest_cand is not None
    assert vitest_cand.cwd == "."
    assert vitest_cand.manifest_file == "package.json"


def test_discover_gotest_candidate(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    candidates = discover_project_candidates(tmp_path)
    gotest_cand = next((c for c in candidates if c.backend == "go-test"), None)
    assert gotest_cand is not None
    assert gotest_cand.cwd == "."
    assert gotest_cand.manifest_file == "go.mod"


def test_discover_maven_candidate(tmp_path: Path):
    (tmp_path / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><artifactId>demo</artifactId></project>",
        encoding="utf-8",
    )
    candidates = discover_project_candidates(tmp_path)
    mvn_cand = next((c for c in candidates if c.backend == "maven-surefire"), None)
    assert mvn_cand is not None
    assert mvn_cand.cwd == "."
    assert mvn_cand.manifest_file == "pom.xml"


def test_discover_gradle_candidate(tmp_path: Path):
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    candidates = discover_project_candidates(tmp_path)
    gradle_cand = next((c for c in candidates if c.backend == "gradle-test"), None)
    assert gradle_cand is not None
    assert gradle_cand.cwd == "."
    assert gradle_cand.manifest_file == "build.gradle"


def test_discovery_never_executes_code_or_spawns_subprocesses(tmp_path: Path):
    # Malicious package.json with dangerous scripts
    (tmp_path / "package.json").write_text(
        '{"name": "evil", "scripts": {"postinstall": "touch /tmp/should_not_exist"}}',
        encoding="utf-8",
    )
    with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
        candidates = discover_project_candidates(tmp_path)
        assert len(candidates) >= 1
        mock_popen.assert_not_called()
        mock_run.assert_not_called()


def test_monorepo_ambiguity_requires_explicit_selection(tmp_path: Path):
    # Monorepo with two distinct subprojects
    sub_a = tmp_path / "packages" / "frontend"
    sub_a.mkdir(parents=True)
    (sub_a / "package.json").write_text(
        '{"name": "frontend", "devDependencies": {"vitest": "^1.0.0"}}',
        encoding="utf-8",
    )

    sub_b = tmp_path / "services" / "backend"
    sub_b.mkdir(parents=True)
    (sub_b / "go.mod").write_text("module example.com/backend\n\ngo 1.22\n", encoding="utf-8")

    # Ambiguity without explicit selection raises AmbiguousSelectionError
    with pytest.raises(AmbiguousSelectionError, match="Multiple candidate test projects discovered"):
        resolve_project_candidate(tmp_path, cwd=None, backend=None)

    # Explicit cwd resolves uniquely
    cand_a = resolve_project_candidate(tmp_path, cwd="packages/frontend", backend=None)
    assert cand_a.backend == "vitest"
    assert cand_a.cwd == "packages/frontend"

    # Explicit backend resolves uniquely
    cand_b = resolve_project_candidate(tmp_path, cwd=None, backend="go-test")
    assert cand_b.backend == "go-test"
    assert cand_b.cwd == "services/backend"


# ============================================================================
# SF-HARNESS-02-A2: Profile freezing & tamper resistance
# ============================================================================

def test_test_profile_deterministic_digest():
    profile1 = TestProfile(
        name="unit-tests",
        backend="pytest",
        cwd=".",
        selection={"test_path": "tests"},
        timeout=45.0,
        sandbox="docker",
        image="selfforge-agent-sandbox:latest",
    )
    profile2 = TestProfile(
        name="unit-tests",
        backend="pytest",
        cwd=".",
        selection={"test_path": "tests"},
        timeout=45.0,
        sandbox="docker",
        image="selfforge-agent-sandbox:latest",
    )
    assert profile1.profile_digest == profile2.profile_digest
    assert len(profile1.profile_digest) == 64

    # Altering timeout alters digest
    profile_diff = TestProfile(
        name="unit-tests",
        backend="pytest",
        cwd=".",
        selection={"test_path": "tests"},
        timeout=60.0,
        sandbox="docker",
        image="selfforge-agent-sandbox:latest",
    )
    assert profile_diff.profile_digest != profile1.profile_digest


def test_profile_freezing_and_disk_mutation_isolation(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    config_path = tmp_path / "selfforge-test.json"
    config_path.write_text(
        '{"name": "default", "backend": "pytest", "cwd": ".", "timeout": 30.0, '
        '"sandbox": "host", "selection": {"test_path": "tests"}}',
        encoding="utf-8",
    )

    profile = load_test_profile(tmp_path)
    req = TestExecutionRequest(
        repository=tmp_path,
        backend=profile.backend,
        cwd=profile.cwd,
        timeout=profile.timeout,
        sandbox="host",
        trusted=True,
    )
    frozen_plan = build_frozen_plan_from_profile(req, profile)
    original_digest = frozen_plan.plan_digest

    # Mutate profile file on disk after plan has been frozen
    config_path.write_text(
        '{"name": "default", "backend": "pytest", "cwd": ".", "timeout": 120.0, '
        '"sandbox": "host", "selection": {"test_path": "tests"}}',
        encoding="utf-8",
    )

    # Already frozen plan is completely unchanged
    assert frozen_plan.timeout == 30.0
    assert frozen_plan.plan_digest == original_digest

    # A subsequent load detects the mutation and produces a different digest
    new_profile = load_test_profile(tmp_path)
    assert new_profile.timeout == 120.0
    assert new_profile.profile_digest != profile.profile_digest


# ============================================================================
# SF-HARNESS-02-A3: Toolchain check & environment unavailable classification
# ============================================================================

def test_missing_toolchain_reports_environment_unavailable(tmp_path: Path):
    harness = TestHarness()
    req = TestExecutionRequest(
        repository=tmp_path,
        backend="vitest",  # Node/Vitest not present in current host environment
        sandbox="host",
        trusted=True,
    )
    # When toolchain or adapter backend is missing/unavailable, returns environment_unavailable
    result = harness.execute_with_availability_check(req)
    assert result.status == HarnessStatus.ERROR
    assert result.reason_code == HarnessReasonCode.ENVIRONMENT_UNAVAILABLE
    assert result.is_success is False


def test_session_environment_conflict_rejection(tmp_path: Path):
    session = SessionStore(tmp_path)
    session.record_environment({
        "version": 1,
        "sandbox": "docker",
        "image_id": "sha256:" + "a" * 64,
    })

    # Resuming with mismatched image must be rejected
    with pytest.raises(ValueError, match="Cannot change a session execution environment"):
        session.record_environment({
            "version": 1,
            "sandbox": "docker",
            "image_id": "sha256:" + "b" * 64,
        })


# ============================================================================
# SF-HARNESS-02-A4: Staging exclusions & dependency isolation
# ============================================================================

def test_staging_excludes_node_modules_and_protected_paths(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staged = tmp_path / "staged"

    # Create workspace files
    (workspace / "src").mkdir()
    (workspace / "src" / "index.js").write_text("console.log('hi');\n", encoding="utf-8")

    # Create sensitive and dependency paths
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "pkg").mkdir()
    (workspace / "node_modules" / "pkg" / "index.js").write_text("module.exports = {};\n")

    (workspace / ".env").write_text("SECRET=1234\n")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("repo config\n")

    # Run staging
    _stage_workspace(workspace, staged, deadline=time.monotonic() + 10.0, cancel=None)

    # Staged directory contains src/index.js
    assert (staged / "src" / "index.js").is_file()

    # Staged directory MUST NOT contain node_modules, .env, .git
    assert not (staged / "node_modules").exists()
    assert not (staged / ".env").exists()
    assert not (staged / ".git").exists()


def test_fingerprint_invariants_preserved(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('v1')\n")
    fp1 = workspace_fingerprint(tmp_path)

    (tmp_path / "app.py").write_text("print('v2')\n")
    fp2 = workspace_fingerprint(tmp_path)

    assert fp1 != fp2
    assert len(fp1) == 64
