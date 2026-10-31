"""Describe traceback associations; location matches cannot establish causality."""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import TestResult

_TRACEBACK_FRAME = re.compile(r'File\s+"([^"]+)",\s+line\s+(\d+)')
_PYTEST_LOCATION = re.compile(r'([^\s:]+\.py):(\d+):')


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
    if head_test is None or head_test.status != "failed" or head_test.failed == 0:
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            confidence="none",
            explanation="Head test did not exhibit assertion or failure regression",
        )

    # Collect failure texts
    texts = [head_test.stdout, head_test.stderr]
    for detail in head_test.failure_details:
        texts.append(detail.message)
        texts.append(detail.detail)

    combined_text = "\n".join(texts)

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

    observed: list[str] = [f"{f}:{l}" for f, l in frames]
    direct_line_match = False
    module_match = False
    best_line = None

    for frame_file, frame_line in frames:
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
