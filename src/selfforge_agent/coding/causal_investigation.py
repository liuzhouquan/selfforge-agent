"""Describe traceback associations; location matches cannot establish causality."""

import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import TestResult

_TRACEBACK_FRAME = re.compile(r'File\s+"([^"]+)",\s+line\s+(\d+)')
_PYTEST_LOCATION = re.compile(r'([^\s:]+\.py):(\d+):')

MAX_INVESTIGATION_TEXT_BYTES = 64 * 1024
MAX_COMBINED_TEXT_BYTES = 128 * 1024


def _truncate_text(text: str | None, max_bytes: int = MAX_INVESTIGATION_TEXT_BYTES) -> str:
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


class CausalInvestigation(BaseModel):
    target: RiskTarget | None = None
    causally_linked: bool
    trace_associated: bool = False
    confidence: Literal["high", "medium", "low", "none"]
    matched_file: bool = False
    matched_line: bool = False
    observed_locations: list[str] = Field(default_factory=list)
    explanation: str


def _normalize_path(path_str: str) -> str:
    return path_str.replace("\\", "/").removeprefix("./")


def investigate_causality(
    target: RiskTarget | None,
    head_test: TestResult | None,
    base_test: TestResult | None = None,
    test_code: str = "",
) -> CausalInvestigation:
    if target is None:
        return CausalInvestigation(
            target=None,
            causally_linked=False,
            confidence="none",
            explanation="No risk target specified for causal investigation",
        )
    if not isinstance(target.line_number, int) or target.line_number < 1 or not math.isfinite(target.line_number):
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            confidence="none",
            explanation=f"Invalid target line number: {target.line_number}",
        )
    if head_test is None or head_test.status != "failed" or head_test.failed == 0:
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            confidence="none",
            explanation="Head test did not exhibit assertion or failure regression",
        )

    # Collect failure texts with bounded sizing to prevent DoS
    texts = [_truncate_text(head_test.stdout), _truncate_text(head_test.stderr)]
    for detail in head_test.failure_details[:50]:
        texts.append(_truncate_text(detail.message, 4096))
        texts.append(_truncate_text(detail.detail, 4096))

    combined_text = "\n".join(texts)
    encoded_comb = combined_text.encode("utf-8", errors="replace")
    if len(encoded_comb) > MAX_COMBINED_TEXT_BYTES:
        combined_text = encoded_comb[:MAX_COMBINED_TEXT_BYTES].decode("utf-8", errors="ignore")

    # Extract stack frames and locations
    frames: list[tuple[str, int]] = []
    for match in _TRACEBACK_FRAME.finditer(combined_text):
        file_path, line_str = match.group(1), match.group(2)
        try:
            frames.append((file_path, int(line_str)))
        except ValueError:
            continue

    for match in _PYTEST_LOCATION.finditer(combined_text):
        file_path, line_str = match.group(1), match.group(2)
        try:
            frames.append((file_path, int(line_str)))
        except ValueError:
            continue

    target_name = _normalize_path(target.file_path)
    target_stem = Path(target.file_path).stem

    observed: list[str] = [f"{f}:{l}" for f, l in frames[:100]]
    direct_line_match = False
    module_match = False
    best_line = None

    for frame_file, frame_line in frames[:100]:
        frame_path = _normalize_path(frame_file)
        if frame_path == target_name or frame_path.endswith("/" + target_name):
            module_match = True
            if frame_line == target.line_number:
                direct_line_match = True
                best_line = frame_line
                break
            if best_line is None:
                best_line = frame_line

    if direct_line_match:
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            trace_associated=True,
            confidence="high",
            matched_file=True,
            matched_line=True,
            observed_locations=observed[:5],
            explanation=(
                f"Head test failure directly traces through target {target.file_path} "
                f"at line {best_line}; this association does not establish the finding's cause"
            ),
        )

    if module_match:
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            trace_associated=True,
            confidence="medium",
            matched_file=True,
            matched_line=False,
            observed_locations=observed[:5],
            explanation=(
                f"Head test failure stack trace passes through target module {target.file_path} "
                f"at line {best_line}; this association does not establish the finding's cause"
            ),
        )

    # Check for caller/assertion referencing target module stem
    if target_stem and (
        f"from {target_stem}" in combined_text
        or f"import {target_stem}" in combined_text
        or target_stem in combined_text
        or f"from {target_stem}" in test_code
        or f"import {target_stem}" in test_code
    ):
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            confidence="low",
            matched_file=False,
            matched_line=False,
            observed_locations=observed[:5],
            explanation=(
                f"Test text mentions {target.file_path}, but no matching failure location "
                "or causal evidence was established"
            ),
        )

    return CausalInvestigation(
        target=target,
        causally_linked=False,
        confidence="none",
        matched_file=False,
        matched_line=False,
        observed_locations=observed[:5],
        explanation=f"Head test failure did not trace through target {target.file_path}",
    )
