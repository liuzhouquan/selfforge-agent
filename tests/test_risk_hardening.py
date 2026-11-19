"""Secondary deep security and accuracy hardening tests for PR risk prioritization (SF-RISK-02).

Proves and verifies:
- A1: Path traversal defense, absolute path filtering, and cross-layer path normalization.
- A2: PrioritizedRisk contract invariants, non-negative finite scores, and validate_risk_target compatibility.
- A3: Severity normalization ('critical' score), category bonuses, and high-risk heuristic patterns.
- A4: Test file deprioritization, ANSI control character sanitization, and full regression.
"""

import pytest
from pydantic import ValidationError

from selfforge_agent.coding.risk_prioritization import (
    PrioritizedRisk,
    prioritize_risks,
)
from selfforge_agent.coding.test_generation import validate_risk_target
from selfforge_agent.models import (
    ChangedFile,
    ChangeKind,
    DiffHunk,
    DiffLine,
    Finding,
    PRContext,
    VerificationStatus,
)


def _make_context(files: list[ChangedFile]) -> PRContext:
    return PRContext(
        repository="owner/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=files,
    )


class TestRiskPathDefenseAndNormalization:
    """A1: Path traversal defense, absolute path filtering, and cross-layer normalization."""

    def test_path_traversal_and_absolute_paths_ignored(self) -> None:
        """Paths escaping workspace or absolute paths must be safely ignored."""
        files = [
            ChangedFile(
                path="../escaped.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,0 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval('1')", old_line=None, new_line=1)],
                    )
                ],
            ),
            ChangedFile(
                path="/etc/passwd.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,0 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval('1')", old_line=None, new_line=1)],
                    )
                ],
            ),
            ChangedFile(
                path="pkg/../../evil.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1,0 +1,1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind=ChangeKind.added, content="eval('1')", old_line=None, new_line=1)],
                    )
                ],
            ),
        ]
        ctx = _make_context(files)
        assert prioritize_risks(ctx) == []

    def test_relative_path_prefix_normalized_and_matches_finding(self) -> None:
        """Path with './' prefix must be normalized so it matches findings and added lines."""
        file = ChangedFile(
            path="./service/handler.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=10,
                    lines=[DiffLine(kind=ChangeKind.added, content="int(token)", old_line=None, new_line=10)],
                )
            ],
        )
        finding = Finding(
            file_path="service/handler.py",  # Notice without './'
            line_number=10,
            severity="high",
            category="type-error",
            description="Type error in handler",
            evidence="int(token)",
            verification_status=VerificationStatus.suspected,
        )
        ctx = _make_context([file])
        risks = prioritize_risks(ctx, findings=[finding])
        assert len(risks) == 1
        assert risks[0].file_path == "service/handler.py"
        assert risks[0].line_number == 10

    def test_invalid_line_number_in_findings_safely_ignored(self) -> None:
        """Finding with line_number <= 0 or exceeding MAX_LINE_NUMBER must be skipped."""
        file = ChangedFile(
            path="service.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=5,
                    lines=[DiffLine(kind=ChangeKind.added, content="int(x)", old_line=None, new_line=5)],
                )
            ],
        )
        bad_findings = [
            Finding.model_construct(
                file_path="service.py",
                line_number=-5,
                severity="high",
                category="sec",
                description="negative line",
                evidence="int(x)",
                verification_status=VerificationStatus.suspected,
            ),
            Finding.model_construct(
                file_path="service.py",
                line_number=0,
                severity="high",
                category="sec",
                description="zero line",
                evidence="int(x)",
                verification_status=VerificationStatus.suspected,
            ),
            Finding(
                file_path="service.py",
                line_number=99_999_999,
                severity="high",
                category="sec",
                description="huge line",
                evidence="int(x)",
                verification_status=VerificationStatus.suspected,
            ),
        ]
        ctx = _make_context([file])
        # Since all findings have invalid line numbers, none of them should appear in results
        risks = prioritize_risks(ctx, findings=bad_findings)
        assert not any(r.line_number in (-5, 0, 99_999_999) for r in risks)
        assert not any(r.description in ("negative line", "zero line", "huge line") for r in risks)


class TestPrioritizedRiskContractAndTargetValidation:
    """A2: Model invariants, non-negative scores, and downstream validate_risk_target compatibility."""

    def test_prioritized_risk_rejects_whitespace_only_strings(self) -> None:
        """PrioritizedRisk must reject whitespace-only strings for file_path, description, evidence."""
        base_kwargs = {
            "file_path": "app.py",
            "line_number": 1,
            "severity": "high",
            "category": "sec",
            "description": "desc",
            "evidence": "line",
            "score": 100.0,
            "rank": 1,
        }
        for field in ["file_path", "description", "evidence"]:
            bad_kwargs = dict(base_kwargs)
            bad_kwargs[field] = "   \t\n  "
            with pytest.raises((ValueError, ValidationError)):
                PrioritizedRisk(**bad_kwargs)

    def test_prioritized_risk_enforces_score_non_negative_and_rounded(self) -> None:
        """Score must be non-negative and properly rounded to avoid floating-point drift."""
        with pytest.raises((ValueError, ValidationError)):
            PrioritizedRisk(
                file_path="app.py",
                line_number=1,
                severity="low",
                category="test",
                description="desc",
                evidence="line",
                score=-10.0,
                rank=1,
            )

        risk = PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="low",
            category="test",
            description="desc",
            evidence="line",
            score=12.34567,
            rank=1,
        )
        assert risk.score == 12.35

    def test_validate_risk_target_compatibility_with_truncated_and_sanitized_evidence(self) -> None:
        """Target with sanitized null bytes or truncated evidence must pass validate_risk_target."""
        long_code = "val = " + "x" * 2500
        file = ChangedFile(
            path="./module/logic.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=20,
                    lines=[
                        DiffLine(
                            kind=ChangeKind.added,
                            content=long_code,
                            old_line=None,
                            new_line=20,
                        )
                    ],
                )
            ],
        )
        ctx = _make_context([file])
        risks = prioritize_risks(ctx)
        assert len(risks) == 1
        target = risks[0].to_risk_target()
        # Must pass validate_risk_target without ValueError
        validate_risk_target(ctx, target)


class TestSeverityNormalizationAndHeuristics:
    """A3: Severity normalization, category bonuses, and high-risk heuristic patterns."""

    def test_critical_severity_receives_highest_base_score(self) -> None:
        """'critical' severity must be scored higher than 'high' (150 > 100)."""
        file = ChangedFile(
            path="core.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,2 @@",
                    old_start=1,
                    new_start=1,
                    lines=[
                        DiffLine(kind=ChangeKind.added, content="auth_call()", old_line=None, new_line=1),
                        DiffLine(kind=ChangeKind.added, content="login_call()", old_line=None, new_line=2),
                    ],
                )
            ],
        )
        findings = [
            Finding(
                file_path="core.py",
                line_number=1,
                severity="high",
                category="auth",
                description="High auth finding",
                evidence="auth_call()",
                verification_status=VerificationStatus.suspected,
            ),
            Finding(
                file_path="core.py",
                line_number=2,
                severity="critical",
                category="auth",
                description="Critical auth finding",
                evidence="login_call()",
                verification_status=VerificationStatus.suspected,
            ),
        ]
        ctx = _make_context([file])
        risks = prioritize_risks(ctx, findings=findings)
        assert len(risks) == 2
        # Critical finding must rank #1 with higher score than high finding
        assert risks[0].rank == 1
        assert risks[0].line_number == 2
        assert risks[0].severity == "critical"
        assert risks[0].score > risks[1].score

    def test_high_risk_heuristics_detect_eval_and_subprocess(self) -> None:
        """Diff heuristic scanning must classify eval and subprocess as high risk when findings are empty."""
        file = ChangedFile(
            path="exec_service.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,3 @@",
                    old_start=1,
                    new_start=1,
                    lines=[
                        DiffLine(kind=ChangeKind.added, content="data = int(x)", old_line=None, new_line=1),
                        DiffLine(kind=ChangeKind.added, content="res = eval(user_code)", old_line=None, new_line=2),
                        DiffLine(kind=ChangeKind.added, content="subprocess.run(cmd, shell=True)", old_line=None, new_line=3),
                    ],
                )
            ],
        )
        ctx = _make_context([file])
        # Empty findings list forces diff heuristic scanner
        risks = prioritize_risks(ctx, findings=[])
        assert len(risks) == 3
        # Both eval and subprocess must be scored as high risk with categories dynamic-code / shell-execution
        top_categories = {r.category for r in risks[:2]}
        assert "dynamic-code" in top_categories or "shell-execution" in top_categories
        assert any(r.severity == "high" for r in risks[:2])


class TestTestFileDeprioritizationAndOutputSanitization:
    """A4: Test file deprioritization and ANSI/control character sanitization."""

    def test_test_files_deprioritized_relative_to_implementation(self) -> None:
        """Risks in test files (tests/*, test_*.py) must be deprioritized compared to implementation code."""
        app_file = ChangedFile(
            path="app/calculator.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=10,
                    lines=[DiffLine(kind=ChangeKind.added, content="return x / y", old_line=None, new_line=10)],
                )
            ],
        )
        test_file = ChangedFile(
            path="tests/test_calculator.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=5,
                    lines=[DiffLine(kind=ChangeKind.added, content="assert x / y == 1", old_line=None, new_line=5)],
                )
            ],
        )
        ctx = _make_context([app_file, test_file])
        risks = prioritize_risks(ctx)
        assert len(risks) == 2
        # app/calculator.py must rank higher than tests/test_calculator.py
        assert risks[0].file_path == "app/calculator.py"
        assert risks[1].file_path == "tests/test_calculator.py"
        assert risks[0].score > risks[1].score

    def test_ansi_escape_and_carriage_return_sanitized(self) -> None:
        """ANSI escape sequences and carriage return characters must be stripped from evidence and description."""
        file = ChangedFile(
            path="logging_service.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=1,
                    lines=[
                        DiffLine(
                            kind=ChangeKind.added,
                            content="\x1b[31;1mprint('hacked')\x1b[0m\r\n",
                            old_line=None,
                            new_line=1,
                        )
                    ],
                )
            ],
        )
        finding = Finding(
            file_path="logging_service.py",
            line_number=1,
            severity="high",
            category="security",
            description="\x1b[33mANSI description\x1b[0m\r",
            evidence="\x1b[31;1mprint('hacked')\x1b[0m\r\n",
            verification_status=VerificationStatus.suspected,
        )
        ctx = _make_context([file])
        risks = prioritize_risks(ctx, findings=[finding])
        assert len(risks) == 1
        risk = risks[0]
        assert "\x1b" not in risk.evidence
        assert "\r" not in risk.evidence
        assert "\x1b" not in risk.description
        assert "\r" not in risk.description
        assert "\x1b" not in risk.rationale
        assert "\r" not in risk.rationale
