"""Automatic PR risk prioritization for targeted test generation."""

import re

from pydantic import BaseModel, Field

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import ChangeKind, Finding, PRContext
from selfforge_agent.review.baseline import BaselineReviewer


class PrioritizedRisk(BaseModel):
    file_path: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    severity: str
    category: str
    description: str
    evidence: str
    score: float
    rank: int = Field(ge=1)
    rationale: str

    def to_risk_target(self) -> RiskTarget:
        return RiskTarget(
            file_path=self.file_path,
            line_number=self.line_number,
            description=self.description,
            evidence=self.evidence,
            category=self.category,
        )


_CATEGORY_BONUSES: dict[str, float] = {
    "dynamic-code": 40.0,
    "shell-execution": 40.0,
    "command-injection": 40.0,
    "sql-injection": 40.0,
    "tls-verification": 25.0,
    "auth": 25.0,
    "security": 25.0,
    "boundary-condition": 30.0,
    "arithmetic-risk": 30.0,
    "null-dereference": 30.0,
    "null-pointer": 30.0,
    "type-error": 30.0,
    "state-mutation": 20.0,
    "resource-leak": 20.0,
    "concurrency": 20.0,
}

_SEVERITY_BASE: dict[str, float] = {
    "high": 100.0,
    "medium": 50.0,
    "low": 20.0,
}

_RISK_HEURISTICS: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"\[-?\d+\]|\.pop\(|\.get\("), "medium", "boundary-condition",
     "Index or collection access boundary risk"),
    (re.compile(r"\s*/\s*|\s*//\s*|\s*%\s*"), "medium", "arithmetic-risk",
     "Division or modulo arithmetic risk"),
    (re.compile(r"\bint\(|\bfloat\(|\bjson\.loads\("), "medium", "type-error",
     "Type conversion or serialization parse risk"),
    (re.compile(r"\bopen\(|\.write\(|\.remove\("), "medium", "state-mutation",
     "State mutation or file resource access"),
    (re.compile(r"\bis\s+None\b|\bassert\b"), "low", "null-dereference",
     "Conditional null or invariant assertion check"),
]


def _score_candidate(severity: str, category: str, evidence: str) -> tuple[float, str]:
    base = _SEVERITY_BASE.get(severity.lower(), 10.0)
    cat_bonus = _CATEGORY_BONUSES.get(category.lower(), 0.0)
    code_bonus = 0.0
    if "=" in evidence:
        code_bonus += 5.0
    if "(" in evidence:
        code_bonus += 5.0
    if evidence.strip().startswith(("def ", "class ")):
        code_bonus -= 10.0
    total = base + cat_bonus + code_bonus
    rationale = f"severity={severity} ({base:.0f}pts) + category={category} ({cat_bonus:.0f}pts)"
    if code_bonus:
        rationale += f" + code_heuristics ({code_bonus:+.0f}pts)"
    return total, rationale


def prioritize_risks(
    context: PRContext,
    findings: list[Finding] | None = None,
    top_k: int = 5,
) -> list[PrioritizedRisk]:
    """Score and rank added-line risk hypotheses from diff and review findings."""
    added_lines: dict[tuple[str, int], str] = {}
    for file in context.files:
        if not file.path.endswith(".py"):
            continue
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.kind == ChangeKind.added and line.new_line is not None:
                    content = line.content.strip() or "<blank added line>"
                    added_lines[(file.path, line.new_line)] = content

    if not added_lines:
        return []

    candidates: list[tuple[str, int, str, str, str, str]] = []
    # (file_path, line_number, severity, category, description, evidence)

    active_findings = findings if findings is not None else BaselineReviewer().review(context)
    for f in active_findings:
        key = (f.file_path, f.line_number)
        if key in added_lines:
            candidates.append((
                f.file_path, f.line_number, f.severity, f.category,
                f.description, added_lines[key],
            ))

    # If no review findings matched added lines, scan diff added lines with risk heuristics
    if not candidates:
        for (file_path, line_no), evidence in added_lines.items():
            if evidence.startswith(("#", '"""', "'''")):
                continue
            matched = False
            for pattern, severity, category, desc in _RISK_HEURISTICS:
                if pattern.search(evidence):
                    candidates.append((file_path, line_no, severity, category, desc, evidence))
                    matched = True
                    break
            if not matched and evidence != "<blank added line>":
                candidates.append((
                    file_path, line_no, "low", "unreviewed-change",
                    "Modified logic in added code", evidence,
                ))

    # Deduplicate by (file_path, line_number, category) taking highest score
    scored_items: dict[tuple[str, int, str], tuple[float, str, str, int, str, str, str, str]] = {}
    for file_path, line_no, severity, category, desc, evidence in candidates:
        key = (file_path, line_no, category)
        score, rationale = _score_candidate(severity, category, evidence)
        if key not in scored_items or score > scored_items[key][0]:
            scored_items[key] = (score, rationale, file_path, line_no, severity, category, desc, evidence)

    # Sort deterministically: score desc, file asc, line asc, category asc
    sorted_candidates = sorted(
        scored_items.values(),
        key=lambda item: (-item[0], item[2], item[3], item[5]),
    )

    results: list[PrioritizedRisk] = []
    for rank, (score, rationale, file_path, line_no, severity, category, desc, evidence) in enumerate(
        sorted_candidates[:top_k], start=1
    ):
        results.append(PrioritizedRisk(
            file_path=file_path,
            line_number=line_no,
            severity=severity,
            category=category,
            description=desc,
            evidence=evidence,
            score=score,
            rank=rank,
            rationale=rationale,
        ))

    return results
