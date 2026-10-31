"""Tests for automatic PR risk prioritization."""

from pathlib import Path

from selfforge_agent.coding.risk_prioritization import (
    PrioritizedRisk,
    prioritize_risks,
)
from selfforge_agent.coding.test_generation import validate_risk_target
from selfforge_agent.core.git_context import build_context
from selfforge_agent.models import Finding, VerificationStatus


def test_prioritize_risks_orders_by_severity_and_category(demo_repo: tuple[Path, str, str]) -> None:
    repository, base, head = demo_repo
    context = build_context(repository, base, head)

    findings = [
        Finding(
            file_path="calculator.py",
            line_number=2,
            severity="medium",
            category="style",
            description="Style issue",
            evidence="eval('1 + 1')",
            verification_status=VerificationStatus.suspected,
        ),
        Finding(
            file_path="calculator.py",
            line_number=2,
            severity="high",
            category="dynamic-code",
            description="Dynamic eval can execute input",
            evidence="eval('1 + 1')",
            verification_status=VerificationStatus.suspected,
        ),
    ]

    prioritized = prioritize_risks(context, findings=findings)
    assert len(prioritized) == 2
    assert prioritized[0].severity == "high"
    assert prioritized[0].category == "dynamic-code"
    assert prioritized[0].rank == 1
    assert prioritized[0].score > prioritized[1].score
    assert prioritized[1].rank == 2

    # Verify risk targets can be converted and validated against context
    target = prioritized[0].to_risk_target()
    validate_risk_target(context, target)


def test_prioritize_risks_without_findings_scans_diff_added_lines(
    demo_repo: tuple[Path, str, str],
) -> None:
    repository, base, head = demo_repo
    context = build_context(repository, base, head)

    prioritized = prioritize_risks(context)
    assert len(prioritized) >= 1
    top = prioritized[0]
    assert isinstance(top, PrioritizedRisk)
    assert top.rank == 1
    assert top.file_path == "calculator.py"
    # Grounding check: must be an added line in the diff
    target = top.to_risk_target()
    validate_risk_target(context, target)
