"""Small deterministic review baseline, deliberately labeled heuristic."""

from __future__ import annotations

import re
from typing import Protocol

from selfforge_agent.models import ChangeKind, Finding, PRContext, VerificationStatus

MAX_REVIEW_FILES = 200
MAX_FILE_HUNKS = 500
MAX_LINE_LENGTH = 10_000
MAX_BASELINE_FINDINGS = 500
MAX_LINE_NUMBER = 10_000_000


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
        if not isinstance(context, PRContext):
            raise TypeError("context must be a valid PRContext instance")

        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()

        for changed in context.files[:MAX_REVIEW_FILES]:
            for hunk in changed.hunks[:MAX_FILE_HUNKS]:
                for line in hunk.lines:
                    if line.kind != ChangeKind.added or line.new_line is None:
                        continue
                    if not (1 <= line.new_line <= MAX_LINE_NUMBER):
                        continue

                    # Bounded content for regex matching and evidence representation
                    content = line.content[:MAX_LINE_LENGTH]
                    for pattern, severity, category, description in _PATTERNS:
                        if pattern.search(content):
                            key = (changed.path, line.new_line, category)
                            if key in seen:
                                continue
                            seen.add(key)
                            findings.append(
                                Finding(
                                    file_path=changed.path,
                                    line_number=line.new_line,
                                    severity=severity,
                                    category=category,
                                    description=description,
                                    evidence=content.strip() or "<blank added line>",
                                    verification_status=VerificationStatus.suspected,
                                )
                            )
                            if len(findings) >= MAX_BASELINE_FINDINGS:
                                return findings
        return findings
