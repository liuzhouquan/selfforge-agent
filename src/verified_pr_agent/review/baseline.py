"""Small deterministic review baseline, deliberately labeled heuristic."""

import re
from typing import Protocol

from verified_pr_agent.models import ChangeKind, Finding, PRContext, VerificationStatus


class ReviewAgent(Protocol):
    def review(self, context: PRContext) -> list[Finding]: ...


_PATTERNS = (
    (re.compile(r"\beval\s*\("), "high", "dynamic-code", "Dynamic eval can execute input"),
    (re.compile(r"\bexec\s*\("), "high", "dynamic-code", "Dynamic exec can execute input"),
    (
        re.compile(r"\bshell\s*=\s*True\b"),
        "high",
        "shell-execution",
        "A subprocess with shell=True may interpret input as commands",
    ),
    (
        re.compile(r"\bverify\s*=\s*False\b"),
        "medium",
        "tls-verification",
        "TLS verification appears disabled",
    ),
)


class BaselineReviewer:
    def review(self, context: PRContext) -> list[Finding]:
        findings: list[Finding] = []
        for changed in context.files:
            for hunk in changed.hunks:
                for line in hunk.lines:
                    if line.kind != ChangeKind.added or line.new_line is None:
                        continue
                    for pattern, severity, category, description in _PATTERNS:
                        if pattern.search(line.content):
                            findings.append(
                                Finding(
                                    file_path=changed.path,
                                    line_number=line.new_line,
                                    severity=severity,
                                    category=category,
                                    description=description,
                                    evidence=line.content.strip(),
                                    verification_status=VerificationStatus.suspected,
                                )
                            )
        return findings
