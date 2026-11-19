"""Small deterministic review baseline, deliberately labeled heuristic."""

from __future__ import annotations

import posixpath
import re
from typing import Protocol

from selfforge_agent.models import ChangeKind, Finding, PRContext, VerificationStatus

MAX_REVIEW_FILES = 200
MAX_FILE_HUNKS = 500
MAX_HUNK_LINES = 1000
MAX_LINE_LENGTH = 10_000
MAX_BASELINE_FINDINGS = 500
MAX_LINE_NUMBER = 10_000_000


def _is_safe_relative_path(path: str) -> bool:
    if not isinstance(path, str) or not path.strip() or len(path) > 512:
        return False
    if any(c in path for c in ("\x00", "\r", "\n", "\t", "\\")):
        return False
    if path.startswith("/"):
        return False
    norm = posixpath.normpath(path.strip())
    return not (norm in (".", "..") or norm.startswith("../") or "/../" in f"/{norm}/")


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

        files = getattr(context, "files", [])
        if not isinstance(files, list):
            return findings

        for changed in files[:MAX_REVIEW_FILES]:
            path = getattr(changed, "path", None)
            if not isinstance(path, str) or not _is_safe_relative_path(path):
                continue
            safe_path = posixpath.normpath(path.strip())

            hunks = getattr(changed, "hunks", [])
            if not isinstance(hunks, list):
                continue

            for hunk in hunks[:MAX_FILE_HUNKS]:
                lines = getattr(hunk, "lines", [])
                if not isinstance(lines, list):
                    continue

                for line in lines[:MAX_HUNK_LINES]:
                    kind = getattr(line, "kind", None)
                    if kind != ChangeKind.added and kind != "added":
                        continue
                    new_line = getattr(line, "new_line", None)
                    if new_line is None or isinstance(new_line, bool) or not isinstance(new_line, int):
                        continue
                    if not (1 <= new_line <= MAX_LINE_NUMBER):
                        continue

                    raw_content = getattr(line, "content", "")
                    if not isinstance(raw_content, str):
                        continue

                    # Bounded content for regex matching and evidence representation (clean null bytes)
                    content = raw_content.replace("\x00", "")[:MAX_LINE_LENGTH]
                    for pattern, severity, category, description in _PATTERNS:
                        if pattern.search(content):
                            key = (safe_path, new_line, category)
                            if key in seen:
                                continue
                            seen.add(key)
                            findings.append(
                                Finding(
                                    file_path=safe_path,
                                    line_number=new_line,
                                    severity=severity,
                                    category=category,
                                    description=description,
                                    evidence=content.strip() or "<blank added line>",
                                    verification_status=VerificationStatus.suspected,
                                )
                            )
                            if len(findings) >= MAX_BASELINE_FINDINGS:
                                findings.sort(key=lambda f: (f.file_path, f.line_number, f.category, f.severity))
                                return findings
        findings.sort(key=lambda f: (f.file_path, f.line_number, f.category, f.severity))
        return findings
