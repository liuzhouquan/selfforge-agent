"""Reproduction tests for regression validation security and isolation hardening (SF-REGRESS-01).

These tests explicitly capture baseline defects before hardening:
- Baseline validate_workspace_regression crashes with unhandled TypeError when repository is not str/Path.
- Baseline validate_workspace_regression crashes with unhandled TypeError when test_file is not str/Path.
- Baseline validate_workspace_regression crashes with ValueError when repository contains null bytes.
- Baseline validate_workspace_regression does not reject test_file paths with trailing slash (directory notation).
- Baseline validate_workspace_regression does not validate workspace_manager type at entry.
- Baseline validate_workspace_regression does not validate legacy_session_roots element types.
- Baseline _validate_base_commit accepts option/flag injection (e.g. '--batch') without regex validation.
- Baseline _validate_base_commit accepts symbolic references ('HEAD', 'main') instead of strict 40/64-hex SHA.
- Baseline _validate_candidate_components accepts empty stem '.py' (hidden file) as test filename.
- Baseline _validate_candidate_components accepts path parts starting with '-' (option injection).
- Baseline _validate_candidate_components does not reject control characters in test path parts.
- Baseline ValidateRegressionRequest accepts blank-only repository or test_file strings.
- Baseline ValidateRegressionRequest accepts null bytes in repository or test_file strings.
- Baseline _run_snapshot_test does not verify that snapshot_dir is not a symlink.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from selfforge_agent.coding.regression_validation import (
    RegressionValidationError,
    RegressionValidationReport,
    ValidateRegressionRequest,
    _run_snapshot_test,
    _validate_base_commit,
    _validate_candidate_components,
    validate_workspace_regression,
)
from selfforge_agent.runtime.workspace import CodingWorkspace


class TestRegressionValidationEntryDefects:
    """Entry parameter validation defects on validate_workspace_regression."""

    @pytest.mark.parametrize("invalid_repo", [None, 123, True, [], {}])
    def test_rejects_non_str_path_repository(self, invalid_repo):
        with pytest.raises(RegressionValidationError, match="Repository path must be a string or Path"):
            validate_workspace_regression(
                repository=invalid_repo,
                session_id="sess-123",
                test_file="tests/test_foo.py",
            )

    @pytest.mark.parametrize("invalid_repo", ["", "   ", "repo\x00pwn", "\x00/repo"])
    def test_rejects_blank_or_null_byte_repository(self, invalid_repo):
        with pytest.raises(RegressionValidationError, match="Repository path is invalid"):
            validate_workspace_regression(
                repository=invalid_repo,
                session_id="sess-123",
                test_file="tests/test_foo.py",
            )

    @pytest.mark.parametrize("invalid_test_file", [None, 123, False, [], {}])
    def test_rejects_non_str_path_test_file(self, invalid_test_file):
        with pytest.raises(RegressionValidationError, match="Candidate test path must be a string or Path"):
            validate_workspace_regression(
                repository="/tmp/repo",
                session_id="sess-123",
                test_file=invalid_test_file,
            )

    @pytest.mark.parametrize("invalid_test_file", ["tests/test_foo.py/", "tests/test_foo.py\\", "foo.py/"])
    def test_rejects_trailing_slash_in_test_file(self, invalid_test_file):
        with pytest.raises(RegressionValidationError, match="Candidate test path must name a file, not a directory"):
            validate_workspace_regression(
                repository="/tmp/repo",
                session_id="sess-123",
                test_file=invalid_test_file,
            )

    def test_rejects_invalid_workspace_manager_type(self):
        with pytest.raises(RegressionValidationError, match="Invalid workspace_manager"):
            validate_workspace_regression(
                repository="/tmp/repo",
                session_id="sess-123",
                test_file="tests/test_foo.py",
                workspace_manager="not-a-manager",
            )

    @pytest.mark.parametrize("bad_roots", [[123], ["/tmp", None], [True]])
    def test_rejects_invalid_legacy_session_roots(self, bad_roots):
        with pytest.raises(RegressionValidationError, match="legacy_session_roots elements must be Path or str"):
            validate_workspace_regression(
                repository="/tmp/repo",
                session_id="sess-123",
                test_file="tests/test_foo.py",
                legacy_session_roots=bad_roots,
            )

    @pytest.mark.parametrize("bad_session", ["", "   ", "sess\x00pwn", "sess/bad", "sess\\bad", "sess..bad", "a" * 300])
    def test_rejects_malformed_session_id(self, bad_session):
        with pytest.raises(RegressionValidationError, match="Invalid session_id"):
            validate_workspace_regression(
                repository="/tmp/repo",
                session_id=bad_session,
                test_file="tests/test_foo.py",
            )


class TestBaseCommitValidationDefects:
    """Defects in base commit SHA validation and injection defense."""

    @pytest.mark.parametrize("bad_sha", [
        "--batch",
        "--output=/tmp/pwn",
        "-f",
        "HEAD",
        "main",
        "v1.0.0",
        "master^1",
        "12345",  # Too short
        "0" * 39,  # 39 chars
        "0" * 41,  # 41 chars (not 40 or 64)
        "g" * 40,  # Non-hex characters
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904\x00",
        " 4b825dc642cb6eb9a060e54bf8d69288fbee4904 ",
    ])
    def test_validate_base_commit_rejects_non_hex_sha_or_flag_injection(self, bad_sha, tmp_path):
        ws = CodingWorkspace(
            repository=tmp_path,
            path=tmp_path / "ws",
            branch="branch-1",
            base_sha=bad_sha,
            session_id="sess-test",
        )
        with pytest.raises(RegressionValidationError, match="Workspace base_sha .* is not a valid commit SHA"):
            _validate_base_commit(ws, deadline=time_module_deadline())


class TestCandidatePathValidationDefects:
    """Defects in candidate path components validation."""

    def test_rejects_empty_stem_dot_py(self):
        with pytest.raises(RegressionValidationError, match="Candidate test (must be a .py file|filename must not be empty)"):
            _validate_candidate_components(Path(".py"))

    @pytest.mark.parametrize("flag_part", ["-test.py", "--flag.py", "tests/-test.py"])
    def test_rejects_leading_hyphen_in_parts(self, flag_part):
        with pytest.raises(RegressionValidationError, match="Path component must not start with hyphen"):
            _validate_candidate_components(Path(flag_part))

    @pytest.mark.parametrize("ctrl_path", ["test\tfoo.py", "test\rbar.py", "test\nbar.py"])
    def test_rejects_control_characters_in_parts(self, ctrl_path):
        with pytest.raises(RegressionValidationError, match="Control character detected"):
            _validate_candidate_components(Path(ctrl_path))


class TestValidateRegressionRequestModelDefects:
    """Defects in API request payload model validation."""

    @pytest.mark.parametrize("bad_repo", ["", "   ", "repo\x00bad", "a" * 5000])
    def test_request_rejects_blank_or_null_or_excessive_repository(self, bad_repo):
        with pytest.raises(ValidationError):
            ValidateRegressionRequest(repository=bad_repo, test_file="tests/test_foo.py")

    @pytest.mark.parametrize("bad_file", ["", "   ", "tests/test_\x00foo.py", "a" * 5000])
    def test_request_rejects_blank_or_null_or_excessive_test_file(self, bad_file):
        with pytest.raises(ValidationError):
            ValidateRegressionRequest(repository="/tmp/repo", test_file=bad_file)


class TestSnapshotTestIsolationDefects:
    """Defects in snapshot test runner isolation."""

    def test_run_snapshot_test_rejects_symlinked_snapshot_dir(self, tmp_path):
        real_dir = tmp_path / "real_snapshot"
        real_dir.mkdir()
        symlink_dir = tmp_path / "sym_snapshot"
        symlink_dir.symlink_to(real_dir)

        runner = MagicMock()
        with pytest.raises(RegressionValidationError, match="Invalid snapshot directory"):
            _run_snapshot_test(
                runner=runner,
                snapshot_dir=symlink_dir,
                candidate_bytes=b"def test_1(): pass\n",
                deadline=time_module_deadline(),
            )


class TestRegressionValidationReportModelInvariants:
    """Invariants on RegressionValidationReport model."""

    def test_report_validates_test_sha256(self):
        valid_report = RegressionValidationReport(
            status="confirmed",
            reason="base_failed_repaired_passed",
            session_id="sess-123",
            base_sha="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            workspace_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            test_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        assert valid_report.test_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    @pytest.mark.parametrize("bad_test_sha", ["", "invalid", "123", "g" * 64])
    def test_report_rejects_invalid_test_sha256(self, bad_test_sha):
        with pytest.raises(ValidationError):
            RegressionValidationReport(
                status="confirmed",
                reason="base_failed_repaired_passed",
                session_id="sess-123",
                base_sha="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
                workspace_sha256="",
                test_sha256=bad_test_sha,
            )


def time_module_deadline() -> float:
    import time
    return time.monotonic() + 30.0
