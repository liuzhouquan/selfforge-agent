"""Describe traceback associations; location matches cannot establish causality."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.models import TestResult

_TRACEBACK_FRAME = re.compile(r'File\s+["\']([^"\'\n\r]+)["\'],\s+line\s+(\d{1,8})(?!\d)')
_PYTEST_LOCATION = re.compile(r'["\']?([^\s:\n\r\'"]+\.py)["\']?:(\d{1,8})(?::|\s+in\s+)')

MAX_INVESTIGATION_TEXT_BYTES = 64 * 1024
MAX_COMBINED_TEXT_BYTES = 128 * 1024
MAX_LINE_NUMBER = 1_000_000
MAX_OBSERVED_FRAMES = 100
MAX_LOCATION_DISPLAY = 5
MAX_LOCATION_PATH_CHARS = 256
MAX_EXPLANATION_CHARS = 2048

GENERIC_STEMS = {
    "test", "tests", "conftest", "utils", "util", "helpers",
    "helper", "common", "base", "core", "app", "main",
    "init", "__init__", "config", "settings", "types", "models",
    "constants", "errors", "exceptions", "compat", "version",
    "schema", "views", "urls", "setup", "cli", "runner",
}

_EXTERNAL_PATH_INDICATORS = (
    "/site-packages/",
    "/dist-packages/",
    "/.venv/",
    "/venv/",
    "/usr/lib/",
    "/usr/local/lib/",
    "/lib/python",
)


def _truncate_text(text: str | None, max_bytes: int = MAX_INVESTIGATION_TEXT_BYTES) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError("text must be a string or None")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    cleaned = text.replace("\0", "")
    encoded = cleaned.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return cleaned
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


class CausalInvestigation(BaseModel):
    target: RiskTarget | None = None
    causally_linked: bool = False
    trace_associated: bool = False
    confidence: Literal["high", "medium", "low", "none"]
    matched_file: bool = False
    matched_line: bool = False
    observed_locations: list[str] = Field(default_factory=list)
    explanation: str

    @field_validator("causally_linked")
    @classmethod
    def _enforce_not_causally_linked(cls, value: bool) -> bool:
        if value is True:
            raise ValueError(
                "Traceback associations cannot establish causality; causally_linked must be False"
            )
        return False

    @field_validator("explanation")
    @classmethod
    def _validate_explanation(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("explanation must be a non-empty string")
        if len(value) > MAX_EXPLANATION_CHARS:
            return value[:MAX_EXPLANATION_CHARS]
        return value


def _normalize_path(path_str: str) -> str:
    if not isinstance(path_str, str):
        raise TypeError("path_str must be a string")
    if "\0" in path_str:
        raise ValueError("path_str must not contain null bytes")

    cleaned = path_str.strip().replace("\\", "/")
    cleaned = re.sub(r"/+", "/", cleaned)
    parts: list[str] = []
    for part in cleaned.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _is_external_frame(norm_path: str) -> bool:
    if norm_path.startswith("<") and norm_path.endswith(">"):
        return True
    path_with_slashes = f"/{norm_path.strip('/')}/"
    return any(indicator in path_with_slashes for indicator in _EXTERNAL_PATH_INDICATORS)


def _sanitize_location_str(file_path: str, line_number: int) -> str:
    cleaned = "".join(char for char in file_path if char.isprintable() and char not in "\n\r\t").strip()
    if len(cleaned) > MAX_LOCATION_PATH_CHARS:
        cleaned = cleaned[:MAX_LOCATION_PATH_CHARS - 3] + "..."
    return f"{cleaned}:{line_number}"


def _extract_frames(combined_text: str, max_frames: int = MAX_OBSERVED_FRAMES) -> list[tuple[str, int]]:
    frames: list[tuple[str, int]] = []
    for match in _TRACEBACK_FRAME.finditer(combined_text):
        file_path, line_str = match.group(1), match.group(2)
        try:
            line_num = int(line_str)
            if 1 <= line_num <= MAX_LINE_NUMBER:
                frames.append((file_path, line_num))
                if len(frames) >= max_frames:
                    return frames
        except (ValueError, OverflowError):
            continue

    for match in _PYTEST_LOCATION.finditer(combined_text):
        file_path, line_str = match.group(1), match.group(2)
        try:
            line_num = int(line_str)
            if 1 <= line_num <= MAX_LINE_NUMBER:
                frames.append((file_path, line_num))
                if len(frames) >= max_frames:
                    return frames
        except (ValueError, OverflowError):
            continue

    return frames


def _collect_test_text(test_result: TestResult | None) -> str:
    if test_result is None:
        return ""
    texts = [_truncate_text(test_result.stdout), _truncate_text(test_result.stderr)]
    details = getattr(test_result, "failure_details", None)
    if isinstance(details, list):
        for detail in details[:50]:
            if detail is None:
                continue
            msg = getattr(detail, "message", None)
            det = getattr(detail, "detail", None)
            if isinstance(msg, str):
                texts.append(_truncate_text(msg, 4096))
            if isinstance(det, str):
                texts.append(_truncate_text(det, 4096))

    combined = "\n".join(texts)
    encoded = combined.encode("utf-8", errors="replace")
    if len(encoded) > MAX_COMBINED_TEXT_BYTES:
        return encoded[:MAX_COMBINED_TEXT_BYTES].decode("utf-8", errors="ignore")
    return combined


def _matches_target(frame_path: str, target_name: str) -> bool:
    norm_frame = _normalize_path(frame_path)
    if _is_external_frame(norm_frame) and not _is_external_frame(target_name):
        return False
    return norm_frame == target_name or norm_frame.endswith("/" + target_name)


def investigate_causality(
    target: RiskTarget | None,
    head_test: TestResult | None,
    base_test: TestResult | None = None,
    test_code: str = "",
) -> CausalInvestigation:
    if target is not None and not isinstance(target, RiskTarget):
        raise TypeError("target must be a RiskTarget instance or None")
    if head_test is not None and not isinstance(head_test, TestResult):
        raise TypeError("head_test must be a TestResult instance or None")
    if base_test is not None and not isinstance(base_test, TestResult):
        raise TypeError("base_test must be a TestResult instance or None")
    if not isinstance(test_code, str):
        raise TypeError("test_code must be a string")
    if "\0" in test_code:
        raise ValueError("test_code must not contain null bytes")

    if target is None:
        return CausalInvestigation(
            target=None,
            causally_linked=False,
            confidence="none",
            explanation="No risk target specified for causal investigation",
        )

    if not isinstance(target.file_path, str) or "\0" in target.file_path:
        raise ValueError("Invalid target file path: must be a string without null bytes")
    if not target.file_path.strip():
        raise ValueError("Invalid target file path: must not be empty or whitespace")

    if type(target.line_number) is not int or not 1 <= target.line_number <= MAX_LINE_NUMBER:
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

    combined_text = _collect_test_text(head_test)
    frames = _extract_frames(combined_text)

    target_name = _normalize_path(target.file_path)
    target_stem = Path(target.file_path).stem

    seen_locs: set[str] = set()
    observed: list[str] = []
    for f, l in frames[:MAX_OBSERVED_FRAMES]:
        loc_str = _sanitize_location_str(f, l)
        if loc_str not in seen_locs:
            seen_locs.add(loc_str)
            observed.append(loc_str)

    direct_line_match = False
    module_match = False
    best_line = None

    for frame_file, frame_line in frames:
        if _matches_target(frame_file, target_name):
            module_match = True
            if frame_line == target.line_number:
                direct_line_match = True
                best_line = frame_line
                break
            if best_line is None:
                best_line = frame_line

    if direct_line_match:
        # Causal disattribution: check if base_test already failed at the exact target location
        if base_test is not None and base_test.status == "failed" and base_test.failed > 0:
            base_text = _collect_test_text(base_test)
            base_frames = _extract_frames(base_text)
            for base_file, base_line in base_frames:
                if _matches_target(base_file, target_name) and base_line == target.line_number:
                    return CausalInvestigation(
                        target=target,
                        causally_linked=False,
                        trace_associated=True,
                        confidence="none",
                        matched_file=True,
                        matched_line=True,
                        observed_locations=observed[:MAX_LOCATION_DISPLAY],
                        explanation=(
                            f"Head test failure matches target {target.file_path} at line {target.line_number}, "
                            "but this failure was already present in base test; cannot establish causal link to head changes"
                        ),
                    )

        return CausalInvestigation(
            target=target,
            causally_linked=False,
            trace_associated=True,
            confidence="high",
            matched_file=True,
            matched_line=True,
            observed_locations=observed[:MAX_LOCATION_DISPLAY],
            explanation=(
                f"Head test failure directly traces through target {target.file_path} "
                f"at line {best_line}; this association does not establish the finding's cause"
            ),
        )

    if module_match:
        # Secondary hardening (SF-CAUSAL-02): check if base_test already failed in the same module
        if base_test is not None and base_test.status == "failed" and base_test.failed > 0:
            base_text = _collect_test_text(base_test)
            base_frames = _extract_frames(base_text)
            for base_file, base_line in base_frames:
                if _matches_target(base_file, target_name):
                    return CausalInvestigation(
                        target=target,
                        causally_linked=False,
                        trace_associated=True,
                        confidence="none",
                        matched_file=True,
                        matched_line=False,
                        observed_locations=observed[:MAX_LOCATION_DISPLAY],
                        explanation=(
                            f"Head test failure stack trace passes through target module {target.file_path} at line {best_line}, "
                            "but this module failure was already present in base test; cannot establish causal link to head changes"
                        ),
                    )

        return CausalInvestigation(
            target=target,
            causally_linked=False,
            trace_associated=True,
            confidence="medium",
            matched_file=True,
            matched_line=False,
            observed_locations=observed[:MAX_LOCATION_DISPLAY],
            explanation=(
                f"Head test failure stack trace passes through target module {target.file_path} "
                f"at line {best_line}; this association does not establish the finding's cause"
            ),
        )

    # Check for caller/assertion referencing target module stem with word boundaries
    test_code_bounded = test_code[:MAX_INVESTIGATION_TEXT_BYTES]
    stem_lower = target_stem.lower()

    stem_matched = False
    if target_stem and len(target_stem) >= 3 and stem_lower not in GENERIC_STEMS:
        pattern = re.compile(
            r"\b(?:import\s+|from\s+[\w.]*\b)" + re.escape(target_stem) + r"\b",
            re.IGNORECASE,
        )
        if pattern.search(combined_text) or pattern.search(test_code_bounded):
            stem_matched = True

    if stem_matched:
        return CausalInvestigation(
            target=target,
            causally_linked=False,
            confidence="low",
            matched_file=False,
            matched_line=False,
            observed_locations=observed[:MAX_LOCATION_DISPLAY],
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
        observed_locations=observed[:MAX_LOCATION_DISPLAY],
        explanation=f"Head test failure did not trace through target {target.file_path}",
    )
