"""Security and boundary tests for PR risk prioritization (SF-RISK-01)."""

import pytest
from pydantic import ValidationError

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.coding.risk_prioritization import (
    MAX_EVIDENCE_CHARS,
    MAX_SCAN_FILES,
    PrioritizedRisk,
    prioritize_risks,
)
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


def test_prioritize_risks_rejects_invalid_context_type() -> None:
    """Non-PRContext context must be rejected with TypeError (fail-closed)."""
    for invalid in [None, "invalid", {"files": []}, 123, []]:
        with pytest.raises(TypeError, match="context must be a PRContext instance"):
            prioritize_risks(invalid)  # type: ignore[arg-type]


def test_prioritize_risks_rejects_invalid_findings_type() -> None:
    """Non-list findings or list containing invalid elements must be rejected with TypeError."""
    ctx = _make_context([])
    for invalid in ["not-a-list", 123, {"key": "val"}, True]:
        with pytest.raises(TypeError, match="findings must be a list of Finding instances or None"):
            prioritize_risks(ctx, findings=invalid)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="findings must contain only Finding instances"):
        prioritize_risks(ctx, findings=["not-a-finding"])  # type: ignore[list-item]


def test_prioritized_risk_boundary_and_null_bytes() -> None:
    """PrioritizedRisk must enforce string length bounds and disallow null bytes."""
    # Disallow null bytes in string fields
    for field_name in ["file_path", "severity", "category", "description", "evidence", "rationale"]:
        kwargs = {
            "file_path": "app.py",
            "line_number": 10,
            "severity": "high",
            "category": "security",
            "description": "desc",
            "evidence": "eval(code)",
            "score": 100.0,
            "rank": 1,
            "rationale": "rationale",
        }
        kwargs[field_name] = "bad\0string"
        with pytest.raises(ValueError, match="null byte"):
            PrioritizedRisk(**kwargs)

    # Disallow strings exceeding maximum lengths
    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="a" * 501,
            line_number=1,
            severity="high",
            category="sec",
            description="desc",
            evidence="line",
            score=10.0,
            rank=1,
            rationale="rat",
        )

    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="a" * 51,
            category="sec",
            description="desc",
            evidence="line",
            score=10.0,
            rank=1,
            rationale="rat",
        )

    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="high",
            category="a" * 101,
            description="desc",
            evidence="line",
            score=10.0,
            rank=1,
            rationale="rat",
        )

    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="high",
            category="sec",
            description="a" * 1001,
            evidence="line",
            score=10.0,
            rank=1,
            rationale="rat",
        )

    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="high",
            category="sec",
            description="desc",
            evidence="a" * 2001,
            score=10.0,
            rank=1,
            rationale="rat",
        )

    with pytest.raises(ValidationError):
        PrioritizedRisk(
            file_path="app.py",
            line_number=1,
            severity="high",
            category="sec",
            description="desc",
            evidence="line",
            score=10.0,
            rank=1,
            rationale="a" * 501,
        )


def test_prioritized_risk_rejects_non_finite_score() -> None:
    """PrioritizedRisk must reject NaN and infinity scores."""
    kwargs = {
        "file_path": "app.py",
        "line_number": 10,
        "severity": "high",
        "category": "security",
        "description": "desc",
        "evidence": "eval(code)",
        "rank": 1,
        "rationale": "rat",
    }
    with pytest.raises(ValueError, match="finite"):
        PrioritizedRisk(score=float("nan"), **kwargs)

    with pytest.raises(ValueError, match="finite"):
        PrioritizedRisk(score=float("inf"), **kwargs)


def test_prioritized_risk_to_risk_target_safety() -> None:
    """PrioritizedRisk.to_risk_target() must always convert to a valid RiskTarget."""
    risk = PrioritizedRisk(
        file_path="pkg/module.py",
        line_number=42,
        severity="high",
        category="dynamic-code",
        description="Dangerous eval execution",
        evidence="eval(user_data)",
        score=145.0,
        rank=1,
        rationale="severity=high + category=dynamic-code",
    )
    target = risk.to_risk_target()
    assert isinstance(target, RiskTarget)
    assert target.file_path == "pkg/module.py"
    assert target.line_number == 42
    assert target.description == "Dangerous eval execution"
    assert target.evidence == "eval(user_data)"
    assert target.category == "dynamic-code"


def test_prioritize_risks_truncates_oversized_evidence_for_risk_target() -> None:
    """Extremely long diff added lines (>2000 chars) must be truncated to avoid RiskTarget ValidationError."""
    long_code = "x = [" + "1, " * 1500 + "2]"
    assert len(long_code) > MAX_EVIDENCE_CHARS

    file = ChangedFile(
        path="huge.py",
        status="M",
        hunks=[
            DiffHunk(
                header="@@ -1,0 +1,1 @@",
                old_start=1,
                new_start=1,
                lines=[DiffLine(kind=ChangeKind.added, content=long_code, old_line=None, new_line=1)],
            )
        ],
    )
    ctx = _make_context([file])
    risks = prioritize_risks(ctx)
    assert len(risks) == 1
    # Evidence must fit within MAX_EVIDENCE_CHARS (2000)
    assert len(risks[0].evidence) <= MAX_EVIDENCE_CHARS
    # to_risk_target() must succeed without ValidationError
    target = risks[0].to_risk_target()
    assert isinstance(target, RiskTarget)
    assert len(target.evidence) <= MAX_EVIDENCE_CHARS


def test_prioritize_risks_bounds_scan_files_and_added_lines() -> None:
    """prioritize_risks must respect MAX_SCAN_FILES and MAX_ADDED_LINES."""
    # Generate 1200 files, each with 15 added lines
    files: list[ChangedFile] = []
    for i in range(1200):
        # File 1050 has an eval line, but since MAX_SCAN_FILES is 1000, it should not be scanned
        content = "eval('hacked')" if i == 1050 else f"y_{i} = {i}"
        hunk = DiffHunk(
            header="@@ -1,0 +1,1 @@",
            old_start=1,
            new_start=1,
            lines=[
                DiffLine(kind=ChangeKind.added, content=content, old_line=None, new_line=1)
            ],
        )
        files.append(ChangedFile(path=f"mod_{i}.py", status="M", hunks=[hunk]))

    ctx = _make_context(files)
    assert len(ctx.files) > MAX_SCAN_FILES

    risks = prioritize_risks(ctx, top_k=10)
    assert len(risks) <= 10
    assert all(r.rank == idx + 1 for idx, r in enumerate(risks))
    # Confirm file 1050 was excluded due to MAX_SCAN_FILES boundary
    assert all(r.file_path != "mod_1050.py" for r in risks)


def test_prioritize_risks_handles_findings_with_null_bytes_or_oversized_fields() -> None:
    """Findings with null bytes or oversized strings must be sanitized without crashing."""
    file = ChangedFile(
        path="target.py",
        status="M",
        hunks=[
            DiffHunk(
                header="@@ -1,0 +1,1 @@",
                old_start=1,
                new_start=1,
                lines=[
                    DiffLine(
                        kind=ChangeKind.added,
                        content="eval(payload)",
                        old_line=None,
                        new_line=5,
                    )
                ],
            )
        ],
    )
    ctx = _make_context([file])
    findings = [
        Finding(
            file_path="target.py",
            line_number=5,
            severity="high\0exploit",
            category="dynamic\0code",
            description="A" * 1500,
            evidence="eval(payload)",
            verification_status=VerificationStatus.suspected,
        )
    ]
    risks = prioritize_risks(ctx, findings=findings)
    assert len(risks) == 1
    top = risks[0]
    assert "\0" not in top.severity
    assert "\0" not in top.category
    assert len(top.description) <= 1000
    target = top.to_risk_target()
    assert isinstance(target, RiskTarget)


def test_prioritize_risks_deterministic_tie_breaking() -> None:
    """prioritize_risks must break ties deterministically: score desc, file asc, line asc, category asc."""
    files = [
        ChangedFile(
            path="b.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=10,
                    lines=[DiffLine(kind=ChangeKind.added, content="int(x)", old_line=None, new_line=10)],
                )
            ],
        ),
        ChangedFile(
            path="a.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,2 @@",
                    old_start=1,
                    new_start=5,
                    lines=[
                        DiffLine(kind=ChangeKind.added, content="int(x)", old_line=None, new_line=5),
                        DiffLine(kind=ChangeKind.added, content="int(y)", old_line=None, new_line=20),
                    ],
                )
            ],
        ),
    ]
    ctx = _make_context(files)
    risks = prioritize_risks(ctx, top_k=5)
    assert len(risks) == 3
    # Same heuristic score: a.py:5 should come before a.py:20, and a.py before b.py:10
    assert risks[0].file_path == "a.py" and risks[0].line_number == 5
    assert risks[1].file_path == "a.py" and risks[1].line_number == 20
    assert risks[2].file_path == "b.py" and risks[2].line_number == 10
    assert [r.rank for r in risks] == [1, 2, 3]


def test_prioritize_risks_top_k_validation() -> None:
    """top_k must be strictly validated between 1 and 100."""
    ctx = _make_context([])
    for bad_top_k in [0, -1, -100, 101, 1000, True, False, "5", 5.5, None]:
        with pytest.raises(ValueError, match="top_k must be an integer between 1 and 100"):
            prioritize_risks(ctx, top_k=bad_top_k)  # type: ignore[arg-type]


def test_prioritize_risks_ignores_non_python_and_malformed_paths() -> None:
    """Non-python files and files with null bytes or oversized paths are safely skipped."""
    files = [
        ChangedFile(
            path="readme.md",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=1,
                    lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", old_line=None, new_line=1)],
                )
            ],
        ),
        ChangedFile(
            path="app\0bad.py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=1,
                    lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", old_line=None, new_line=1)],
                )
            ],
        ),
        ChangedFile(
            path="dir/" + "a" * 501 + ".py",
            status="M",
            hunks=[
                DiffHunk(
                    header="@@ -1,0 +1,1 @@",
                    old_start=1,
                    new_start=1,
                    lines=[DiffLine(kind=ChangeKind.added, content="eval(1)", old_line=None, new_line=1)],
                )
            ],
        ),
    ]
    ctx = _make_context(files)
    assert prioritize_risks(ctx) == []


def test_default_reviewer_receives_only_bounded_scan_context(monkeypatch):
    import selfforge_agent.coding.risk_prioritization as module

    line = DiffLine(kind=ChangeKind.added, content="int(value)", new_line=1)
    hunk = DiffHunk(header="@@", old_start=1, new_start=1, lines=[line] * 10001)
    file = ChangedFile(path="app.py", status="modified", hunks=[hunk])
    empty = ChangedFile(path="empty.py", status="modified", hunks=[])
    context = _make_context([]).model_copy(update={"files": [file] + [empty] * MAX_SCAN_FILES})
    observed = []

    def review(_self, bounded):
        observed.append(bounded)
        assert len(bounded.files) <= MAX_SCAN_FILES
        assert sum(len(h.lines) for f in bounded.files for h in f.hunks) <= 10000
        return []

    monkeypatch.setattr(module.BaselineReviewer, "review", review)
    assert prioritize_risks(context)
    assert observed
    assert len(context.files) == MAX_SCAN_FILES + 1
    assert len(context.files[0].hunks[0].lines) == 10001
