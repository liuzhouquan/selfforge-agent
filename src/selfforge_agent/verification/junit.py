"""Parse counts and bounded diagnostics from the same pytest JUnit document."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from selfforge_agent.models import TestFailure

MAX_JUNIT_BYTES = 8 * 1024 * 1024
MAX_FAILURE_DETAILS = 5


@dataclass
class JUnitEvidence:
    counts: dict[str, int] = field(default_factory=lambda: {
        key: 0 for key in ("passed", "failed", "errors", "skipped")})
    failures: list[TestFailure] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def _bounded(text: str, limit: int, *, multiline: bool = False) -> tuple[str, bool]:
    text = ''.join(char for char in text if char.isprintable() or char in "\n\t")
    if not multiline:
        text = ' '.join(text.split())
    return text[:limit], len(text) > limit


def parse_junit(xml: bytes | None, timed_out: bool) -> JUnitEvidence:
    evidence = JUnitEvidence()
    if xml is None:
        evidence.error = None if timed_out else "pytest did not produce JUnit XML"
        return evidence
    try:
        if len(xml) > MAX_JUNIT_BYTES:
            raise ValueError("JUnit report exceeds 8 MiB")
        if b"\x00" in xml or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("JUnit requires UTF-8 XML without DTD or entity declarations")
        root = ET.fromstring(xml)
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites:
            raise ValueError("No testsuite element")
        for suite in suites:
            # pytest emits flat suites. Do not double-count nested aggregate totals.
            if suite.findall(".//testsuite"):
                raise ValueError("Nested testsuite totals are unsupported")
            total, failed, errors, skipped = (
                int(suite.get(key, "0")) for key in ("tests", "failures", "errors", "skipped")
            )
            if min(total, failed, errors, skipped) < 0 or failed + errors + skipped > total:
                raise ValueError("Inconsistent testsuite counts")
            if ((suite.findall("testcase/failure") and failed == 0)
                    or (suite.findall("testcase/error") and errors == 0)):
                raise ValueError("Failure elements disagree with testsuite counts")
            evidence.counts["failed"] += failed
            evidence.counts["errors"] += errors
            evidence.counts["skipped"] += skipped
            evidence.counts["passed"] += total - failed - errors - skipped
        # Extract only after every suite has passed validation.
        for case in root.iter("testcase"):
            for child in case:
                if child.tag not in {"failure", "error"}:
                    continue
                if len(evidence.failures) >= MAX_FAILURE_DETAILS:
                    evidence.truncated = True
                    continue
                raw_id = "::".join(filter(None, (case.get("classname"), case.get("name"))))
                test_id, id_cut = _bounded(raw_id or "unknown", 256)
                message, message_cut = _bounded(child.get("message") or "", 256)
                detail, detail_cut = _bounded(child.text or "", 1200, multiline=True)
                evidence.truncated |= id_cut or message_cut or detail_cut
                evidence.failures.append(TestFailure(test_id=test_id, kind=child.tag,
                                                      message=message, detail=detail))
        return evidence
    except (ET.ParseError, ValueError) as exc:
        return JUnitEvidence(error=f"Invalid JUnit XML: {exc}")
