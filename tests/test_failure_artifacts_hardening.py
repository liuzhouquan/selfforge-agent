"""Secondary deep security and lifecycle hardening tests for failure artifacts (SF-FAIL-01).

Proves and verifies:
- A1: Strict model schema, fail-closed parameter types, non-empty reasons, and null-byte defenses.
- A2: Bidirectional repository isolation, symlink defenses, and atomic collision handling.
- A3: Oversized diagnostic payload graceful truncation and boundary defense under 128 KiB.
- A4: Deterministic failure listing (list_failures), aggregation (summarize_failures), and temp cleanup.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from selfforge_agent.models import TestFailure, TestResult
from selfforge_agent.runtime.failure_artifacts import (
    MAX_ARTIFACT_BYTES,
    FailureArtifact,
    cleanup_temporary_artifacts,
    list_failures,
    prepare_artifacts,
    read_failure,
    summarize_failures,
    write_failure,
)

TestFailure.__test__ = False
TestResult.__test__ = False


def _make_test_result(
    status: str = "failed",
    failed: int = 1,
    exit_code: int = 1,
    duration: float = 1.0,
    error_message: str | None = "assertion error",
    failure_details: list[TestFailure] | None = None,
) -> TestResult:
    return TestResult(
        command=["pytest"],
        environment="test-env",
        exit_code=exit_code,
        duration_seconds=duration,
        passed=0,
        failed=failed,
        errors=0,
        skipped=0,
        status=status,
        stdout="STDOUT",
        stderr="STDERR",
        error_message=error_message,
        failure_details=failure_details or [],
    )


class TestFailureArtifactInputGatingAndModelSchema:
    """A1: Model schema, fail-closed input gating, and reason sanitization."""

    def test_write_failure_rejects_invalid_visible_or_holdout_types(self, tmp_path: Path) -> None:
        """Non-TestResult visible or holdout must be rejected with TypeError (fail-closed)."""
        root = tmp_path / "artifacts"
        root.mkdir()

        for invalid in ["not-a-result", 123, {"passed": 0}, [1, 2, 3]]:
            with pytest.raises(TypeError, match="visible|holdout|TestResult"):
                write_failure(
                    root,
                    case="addition",
                    repeat=0,
                    reason="failed",
                    visible=invalid,  # type: ignore[arg-type]
                    holdout=None,
                )

            with pytest.raises(TypeError, match="visible|holdout|TestResult"):
                write_failure(
                    root,
                    case="addition",
                    repeat=0,
                    reason="failed",
                    visible=None,
                    holdout=invalid,  # type: ignore[arg-type]
                )

    def test_write_failure_rejects_empty_or_whitespace_or_null_byte_reason(self, tmp_path: Path) -> None:
        """Reason must not be empty, whitespace-only, or contain null bytes."""
        root = tmp_path / "artifacts"
        root.mkdir()

        for bad_reason in ("", "   ", "\t\n  ", "fail\0bad", "bad\x1b[31mcolor"):
            with pytest.raises(ValueError, match="reason|Invalid"):
                write_failure(
                    root,
                    case="addition",
                    repeat=0,
                    reason=bad_reason,
                    visible=None,
                    holdout=None,
                )

    def test_failure_artifact_pydantic_model_validation(self) -> None:
        """FailureArtifact Pydantic model must validate schema, version, case, and test summaries."""
        # Valid construction
        artifact = FailureArtifact(
            version=1,
            case="subtraction",
            repeat=2,
            reason="timeout",
            visible=None,
            holdout={"exit_code": 1, "duration_seconds": 2.5, "passed": 0, "failed": 1, "errors": 0, "skipped": 0, "status": "failed"},
        )
        assert artifact.case == "subtraction"
        assert artifact.repeat == 2
        assert artifact.version == 1

        # Invalid version != 1
        with pytest.raises((ValueError, ValidationError)):
            FailureArtifact(
                version=2,
                case="subtraction",
                repeat=2,
                reason="timeout",
            )

        # Invalid case name
        with pytest.raises((ValueError, ValidationError)):
            FailureArtifact(
                version=1,
                case="../../escape",
                repeat=0,
                reason="timeout",
            )


class TestRepositoryIsolationAndCollisionDefense:
    """A2: Bidirectional repository isolation, symlink prevention, and duplicate collision."""

    def test_prepare_artifacts_rejects_bidirectional_repo_nesting(self, tmp_path: Path) -> None:
        """Artifact directory cannot contain repo, and repo cannot contain artifact directory."""
        # Case 1: artifacts inside repo (already tested in RUN-08)
        repo = tmp_path / "my_repo"
        repo.mkdir()
        with pytest.raises(ValueError, match="stay outside"):
            prepare_artifacts(repo / "artifacts", [repo])

        # Case 2: repo inside artifacts directory
        artifacts_dir = tmp_path / "artifacts_outer"
        artifacts_dir.mkdir()
        nested_repo = artifacts_dir / "nested_repo"
        nested_repo.mkdir()
        with pytest.raises(ValueError, match="stay outside|conflict"):
            prepare_artifacts(artifacts_dir, [nested_repo])

    def test_write_failure_raises_controlled_error_on_collision(self, tmp_path: Path) -> None:
        """Writing an existing artifact name must fail with controlled FileExistsError or ValueError."""
        root = tmp_path / "artifacts"
        root.mkdir()

        res = _make_test_result()
        name1 = write_failure(root, case="test_case", repeat=0, reason="failed", visible=res, holdout=None)
        assert name1 == "test_case-0.json"

        # Attempting to overwrite the exact same case and repeat
        with pytest.raises((FileExistsError, ValueError), match="exists|collision"):
            write_failure(root, case="test_case", repeat=0, reason="second attempt", visible=res, holdout=None)


class TestOversizedPayloadTruncationAndBoundary:
    """A3: Graceful truncation of oversized error message / failure details under 128 KiB."""

    def test_write_failure_gracefully_truncates_large_error_message(self, tmp_path: Path) -> None:
        """Oversized error_message in TestResult is safely truncated rather than crashing write_failure."""
        root = tmp_path / "artifacts"
        root.mkdir()

        # Create TestResult with > 150 KiB error message
        huge_error = "Traceback (most recent call last):\n" + "    line in module\n" * 10000
        assert len(huge_error.encode("utf-8")) > MAX_ARTIFACT_BYTES

        res = _make_test_result(error_message=huge_error)
        filename = write_failure(root, case="overflow_case", repeat=1, reason="harness error", visible=res, holdout=None)

        # File must exist and be strictly bounded within MAX_ARTIFACT_BYTES
        artifact_path = root / filename
        assert artifact_path.is_file()
        assert artifact_path.stat().st_size <= MAX_ARTIFACT_BYTES

        # Must be validly readable
        data = read_failure(root, filename)
        assert data["case"] == "overflow_case"
        assert "[truncated]" in data["visible"]["error_message"]

    def test_write_failure_both_visible_and_holdout_large_payloads(self, tmp_path: Path) -> None:
        """Both visible and holdout with large error messages are bounded safely under 128 KiB."""
        root = tmp_path / "artifacts"
        root.mkdir()

        large_error = "Error output line\n" * 5000
        visible_res = _make_test_result(error_message=large_error)
        holdout_res = _make_test_result(error_message=large_error)

        filename = write_failure(
            root, case="dual_large", repeat=0, reason="both failed",
            visible=visible_res, holdout=holdout_res,
        )

        artifact_path = root / filename
        assert artifact_path.is_file()
        assert artifact_path.stat().st_size <= MAX_ARTIFACT_BYTES

        data = read_failure(root, filename)
        assert data["visible"]["error_message"] is not None
        assert data["holdout"]["error_message"] is not None


class TestDeterministicListingAggregationAndCleanup:
    """A4: Deterministic listing (list_failures), aggregation (summarize_failures), and temp cleanup."""

    def test_list_failures_returns_deterministic_sorted_artifacts(self, tmp_path: Path) -> None:
        """list_failures must return filenames sorted deterministically by (case, repeat) and ignore noise."""
        root = tmp_path / "artifacts"
        root.mkdir()

        res = _make_test_result()
        # Write out of order
        write_failure(root, case="case_b", repeat=2, reason="failed", visible=res, holdout=None)
        write_failure(root, case="case_a", repeat=0, reason="failed", visible=res, holdout=None)
        write_failure(root, case="case_b", repeat=0, reason="failed", visible=res, holdout=None)
        write_failure(root, case="case_a", repeat=1, reason="failed", visible=res, holdout=None)

        # Add some noise files
        (root / "unrelated.txt").write_text("noise")
        (root / "case_a-bad.json").write_text("{}")
        (root / ".hidden").write_text("secret")

        failures = list_failures(root)
        expected = [
            "case_a-0.json",
            "case_a-1.json",
            "case_b-0.json",
            "case_b-2.json",
        ]
        assert failures == expected

    def test_list_failures_rejects_symlink_root(self, tmp_path: Path) -> None:
        """list_failures must fail-closed if root directory is a symlink."""
        real_root = tmp_path / "real_artifacts"
        real_root.mkdir()
        sym_root = tmp_path / "sym_artifacts"
        sym_root.symlink_to(real_root)

        with pytest.raises(ValueError, match="non-symlink"):
            list_failures(sym_root)

    def test_summarize_failures_aggregates_diagnostics(self, tmp_path: Path) -> None:
        """summarize_failures must correctly tally failure counts, cases, and reason breakdown."""
        root = tmp_path / "artifacts"
        root.mkdir()

        res1 = _make_test_result(error_message="assertion error")
        res2 = _make_test_result(error_message="timeout")

        write_failure(root, case="math_add", repeat=0, reason="assertion_failure", visible=res1, holdout=None)
        write_failure(root, case="math_add", repeat=1, reason="assertion_failure", visible=res1, holdout=None)
        write_failure(root, case="math_div", repeat=0, reason="timeout", visible=res2, holdout=None)

        summary = summarize_failures(root)
        assert summary.total_failures == 3
        assert set(summary.failed_cases) == {"math_add", "math_div"}
        assert summary.reasons["assertion_failure"] == 2
        assert summary.reasons["timeout"] == 1
        assert len(summary.artifacts) == 3

    def test_cleanup_temporary_artifacts_removes_orphaned_tmp_files(self, tmp_path: Path) -> None:
        """cleanup_temporary_artifacts must remove .*.tmp files while preserving valid artifacts."""
        root = tmp_path / "artifacts"
        root.mkdir()

        res = _make_test_result()
        valid = write_failure(root, case="task", repeat=0, reason="failed", visible=res, holdout=None)

        # Simulate left-over interrupted temp files
        tmp1 = root / ".task-0.12345.tmp"
        tmp1.write_text("partial")
        tmp2 = root / ".task-1.67890.tmp"
        tmp2.write_text("partial")

        removed = cleanup_temporary_artifacts(root)
        assert removed == 2
        assert not tmp1.exists()
        assert not tmp2.exists()
        assert (root / valid).exists()


@pytest.mark.parametrize('field,value', [('version', True), ('repeat', True), ('visible', {'passed': -1})])
def test_artifact_model_matches_strict_disk_schema(field, value):
    payload = {'case': 'fixture', 'repeat': 0, 'reason': 'failed', field: value}
    with pytest.raises(ValidationError):
        FailureArtifact(**payload)


def test_artifact_preparation_rejects_ancestor_symlink(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        prepare_artifacts(alias / 'artifacts', [])


@pytest.mark.parametrize('operation', ['read', 'write', 'list', 'cleanup'])
def test_artifact_operations_reject_ancestor_symlink(tmp_path, operation):
    real = tmp_path / 'real'
    real.mkdir()
    root = prepare_artifacts(real / 'artifacts', [])
    filename = write_failure(root, case='fixture', repeat=0, reason='failed', visible=None, holdout=None)
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    linked = alias / root.relative_to(real)
    with pytest.raises((ValueError, OSError)):
        if operation == 'read':
            read_failure(linked, filename)
        elif operation == 'write':
            write_failure(linked, case='fixture', repeat=1, reason='failed', visible=None, holdout=None)
        elif operation == 'list':
            list_failures(linked)
        else:
            cleanup_temporary_artifacts(linked)


def test_failure_reason_rejects_terminal_osc_sequence(tmp_path):
    with pytest.raises(ValueError, match='ANSI|control'):
        write_failure(tmp_path, case='fixture', repeat=0, reason='\x1b]0;spoof\x07failed', visible=None, holdout=None)


@pytest.mark.parametrize('relation', ['inside_repository', 'contains_repository'])
def test_artifact_isolation_rejects_raw_parent_segments_without_writes(tmp_path, relation):
    repo = tmp_path / 'repo'
    repo.mkdir()
    prefix = tmp_path / 'prefix'
    prefix.mkdir()
    directory = prefix / '..'
    if relation == 'inside_repository':
        directory = directory / 'repo' / 'artifacts'
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    with pytest.raises(ValueError):
        prepare_artifacts(directory, [repo])
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == before
