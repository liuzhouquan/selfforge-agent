"""Automatic PR risk prioritization for targeted test generation."""

import math
import posixpath
import re

from pydantic import BaseModel, Field, field_validator

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import ChangeKind, Finding, PRContext
from selfforge_agent.review.baseline import BaselineReviewer

MAX_SCAN_FILES = 1_000
MAX_SCAN_HUNKS = 1_000
MAX_SCAN_LINES = 10_000
MAX_ADDED_LINES = 10_000
MAX_FINDINGS = 1_000
MAX_EVIDENCE_CHARS = 2_000
MAX_LINE_NUMBER = 10_000_000
_TEST_FILE_PENALTY = -50.0

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

__all__ = [
    "MAX_ADDED_LINES",
    "MAX_EVIDENCE_CHARS",
    "MAX_FINDINGS",
    "MAX_LINE_NUMBER",
    "MAX_SCAN_FILES",
    "PrioritizedRisk",
    "prioritize_risks",
]


def _clean_text(text: str) -> str:
    """Strip ANSI escape sequences, carriage returns, null bytes, and control artifacts."""
    no_ansi = _ANSI_ESCAPE_RE.sub("", text)
    return no_ctrl if (no_ctrl := no_ansi.replace("\0", "").replace("\r", "")) else ""


def _normalize_safe_file_path(path: str) -> str | None:
    """Normalize path and ensure it does not escape workspace or contain invalid segments."""
    if not isinstance(path, str) or not path.strip() or "\0" in path or len(path) > 500:
        return None
    clean = path.strip().replace("\\", "/")
    norm = posixpath.normpath(clean)
    if norm.startswith(("/", "../")) or norm in ("..", "."):
        return None
    parts = norm.split("/")
    if ".." in parts:
        return None
    return norm


def _is_test_file(path: str) -> bool:
    """Check if a file path is a test file or test runner artifact."""
    parts = [p.lower() for p in path.split("/")]
    filename = parts[-1]
    if any(part in ("tests", "test", "testing") for part in parts[:-1]):
        return True
    return bool(filename.startswith("test_") or filename.endswith("_test.py") or filename == "conftest.py")


class PrioritizedRisk(BaseModel):
    file_path: str = Field(min_length=1, max_length=500)
    line_number: int = Field(ge=1, le=MAX_LINE_NUMBER)
    severity: str = Field(min_length=1, max_length=50)
    category: str = Field(default="", max_length=100)
    description: str = Field(min_length=1, max_length=1_000)
    evidence: str = Field(min_length=1, max_length=2_000)
    score: float
    rank: int = Field(ge=1)
    rationale: str = Field(default="", max_length=500)

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("null byte is not allowed")
        if not value or not value.strip():
            raise ValueError("file_path cannot be empty or whitespace")
        norm = _normalize_safe_file_path(value)
        if norm is None:
            raise ValueError(f"invalid or unsafe file path: {value}")
        return norm

    @field_validator("description", "evidence")
    @classmethod
    def _validate_non_empty_content(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value cannot be empty or whitespace")
        if "\0" in value:
            raise ValueError("null byte is not allowed")
        cleaned = _clean_text(value).strip()
        if not cleaned:
            raise ValueError("value cannot be empty after sanitization")
        return cleaned

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("null byte is not allowed")
        cleaned = _clean_text(value).strip().lower()
        if not cleaned:
            raise ValueError("severity cannot be empty after sanitization")
        return cleaned

    @field_validator("category", "rationale")
    @classmethod
    def _validate_category_and_rationale(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("null byte is not allowed")
        return _clean_text(value).strip()

    @field_validator("score")
    @classmethod
    def _validate_score_finite_and_positive(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("score must be a finite number")
        if value < 0.0:
            raise ValueError("score cannot be negative")
        return round(float(value), 2)

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
    "path-traversal": 40.0,
    "deserialization": 40.0,
    "ssrf": 40.0,
    "boundary-condition": 30.0,
    "arithmetic-risk": 30.0,
    "null-dereference": 30.0,
    "null-pointer": 30.0,
    "type-error": 30.0,
    "tls-verification": 25.0,
    "auth": 25.0,
    "security": 25.0,
    "crypto": 25.0,
    "redos": 25.0,
    "state-mutation": 20.0,
    "resource-leak": 20.0,
    "concurrency": 20.0,
}

_SEVERITY_BASE: dict[str, float] = {
    "critical": 150.0,
    "high": 100.0,
    "medium": 50.0,
    "low": 20.0,
    "info": 10.0,
    "informational": 10.0,
}

_RISK_HEURISTICS: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"\beval\s*\(|\bexec\s*\("), "high", "dynamic-code",
     "Dynamic code evaluation risk"),
    (re.compile(r"\bos\.system\s*\(|\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\(|\bshell\s*=\s*True\b"),
     "high", "shell-execution", "Shell or subprocess execution risk"),
    (re.compile(r"\bpickle\.loads?\s*\(|\byaml\.(?:load|unsafe_load)\s*\("),
     "high", "deserialization", "Untrusted deserialization risk"),
    (re.compile(r"cursor\.execute\s*\(|\braw_sql\s*\(|\bSELECT\b.*\bFROM\b", re.IGNORECASE),
     "high", "sql-injection", "Potential SQL execution injection risk"),
    (re.compile(r"\bverify\s*=\s*False\b"), "medium", "tls-verification",
     "Disabled TLS certificate verification"),
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


def _score_candidate(file_path: str, severity: str, category: str, evidence: str) -> tuple[float, str]:
    base = _SEVERITY_BASE.get(severity.lower(), 10.0)
    cat_bonus = _CATEGORY_BONUSES.get(category.lower(), 0.0)
    code_bonus = 0.0
    if "=" in evidence:
        code_bonus += 5.0
    if "(" in evidence:
        code_bonus += 5.0
    if evidence.strip().startswith(("def ", "class ")):
        code_bonus -= 10.0

    test_penalty = _TEST_FILE_PENALTY if _is_test_file(file_path) else 0.0

    total = max(0.0, base + cat_bonus + code_bonus + test_penalty)
    rationale = f"severity={severity} ({base:.0f}pts) + category={category} ({cat_bonus:.0f}pts)"
    if code_bonus:
        rationale += f" + code_heuristics ({code_bonus:+.0f}pts)"
    if test_penalty:
        rationale += f" + test_file_penalty ({test_penalty:+.0f}pts)"
    return round(total, 2), _clean_text(rationale)[:500]


def _bounded_context(context: PRContext) -> PRContext:
    files = []
    remaining_lines = MAX_SCAN_LINES
    remaining_hunks = MAX_SCAN_HUNKS
    for file in context.files[:MAX_SCAN_FILES]:
        hunks = []
        for hunk in file.hunks[:remaining_hunks]:
            lines = hunk.lines[:remaining_lines]
            hunks.append(hunk.model_copy(update={"lines": lines}))
            remaining_hunks -= 1
            remaining_lines -= len(lines)
            if remaining_lines == 0 or remaining_hunks == 0:
                break
        files.append(file.model_copy(update={"hunks": hunks}))
        if remaining_lines == 0 or remaining_hunks == 0:
            break
    return context.model_copy(update={"files": files})


def prioritize_risks(
    context: PRContext,
    findings: list[Finding] | None = None,
    top_k: int = 5,
) -> list[PrioritizedRisk]:
    """Score and rank added-line risk hypotheses from diff and review findings."""
    if not isinstance(context, PRContext):
        raise TypeError("context must be a PRContext instance")
    if not isinstance(context.files, list):
        raise TypeError("context.files must be a list of ChangedFile instances")
    if type(top_k) is not int or top_k < 1 or top_k > 100:
        raise ValueError("top_k must be an integer between 1 and 100")
    if findings is not None:
        if not isinstance(findings, list):
            raise TypeError("findings must be a list of Finding instances or None")
        for f in findings:
            if not isinstance(f, Finding):
                raise TypeError("findings must contain only Finding instances")

    context = _bounded_context(context)
    added_lines: dict[tuple[str, int], str] = {}
    for file in context.files[:MAX_SCAN_FILES]:
        norm_path = _normalize_safe_file_path(file.path)
        if norm_path is None or not norm_path.endswith(".py"):
            continue
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.kind == ChangeKind.added and isinstance(line.new_line, int) and (1 <= line.new_line <= MAX_LINE_NUMBER):
                    raw_content = _clean_text(line.content).strip() or "<blank added line>"
                    content = raw_content[:MAX_EVIDENCE_CHARS]
                    added_lines[(norm_path, line.new_line)] = content
                    if len(added_lines) >= MAX_ADDED_LINES:
                        break
            if len(added_lines) >= MAX_ADDED_LINES:
                break
        if len(added_lines) >= MAX_ADDED_LINES:
            break

    if not added_lines:
        return []

    candidates: list[tuple[str, int, str, str, str, str]] = []
    # (file_path, line_number, severity, category, description, evidence)

    active_findings: list[Finding]
    if findings is not None:
        active_findings = findings[:MAX_FINDINGS]
    else:
        active_findings = BaselineReviewer().review(context)[:MAX_FINDINGS]

    for f in active_findings:
        if not isinstance(f.file_path, str) or not isinstance(f.line_number, int):
            continue
        if not (1 <= f.line_number <= MAX_LINE_NUMBER):
            continue
        norm_f_path = _normalize_safe_file_path(f.file_path)
        if norm_f_path is None:
            continue
        key = (norm_f_path, f.line_number)
        if key in added_lines:
            sev = _clean_text(f.severity or "medium").strip()[:50] or "medium"
            cat = _clean_text(f.category or "review-finding").strip()[:100]
            desc = _clean_text(f.description or "Review finding").strip()[:1000] or "Review finding"
            candidates.append((
                norm_f_path, f.line_number, sev, cat,
                desc, added_lines[key],
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
        score, rationale = _score_candidate(file_path, severity, category, evidence)
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
            rationale=rationale[:500],
        ))

    return results
