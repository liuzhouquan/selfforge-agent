"""Diff and regression analysis for benchmark evaluation reports (SF-EVAL-05)."""

import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any


def read_eval_report(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evaluation report must be a regular file, not a symlink")
    with path.open("rb") as handle:
        data = handle.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("Evaluation report exceeds 32 MiB")
    try:
        return json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid evaluation JSON report") from exc


def _count(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or not (1 if positive else 0) <= value <= 10**12:
        raise ValueError("Report counts must be nonnegative integers and runs must be positive")
    return value


def _finite(value: object) -> float:
    try:
        if type(value) not in {float, int} or not math.isfinite(value) or value < 0:
            raise ValueError("Report numeric metrics must be finite and nonnegative")
        return float(value)
    except OverflowError as exc:
        raise ValueError("Report numeric metric overflow") from exc


def _checked(report: object) -> dict:
    if not isinstance(report, dict):
        raise ValueError("Expected an evaluation report object")  # noqa: TRY004 - report data
    result = report.copy()
    sha = report.get("manifest_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("Report requires a complete manifest SHA-256")
    runs, successes = _count(report.get("runs"), positive=True), _count(report.get("successes"))
    cases = report.get("by_case")
    if not isinstance(cases, dict) or not cases or len(cases) > 10000:
        raise ValueError("Report requires a nonempty bounded by_case mapping")
    sum_runs = sum_successes = 0
    for name, case in cases.items():
        if not isinstance(name, str) or not name or len(name) > 200 or not isinstance(case, dict):
            raise ValueError("Invalid case record")
        cr, cs = _count(case.get("runs"), positive=True), _count(case.get("successes"))
        if cs > cr:
            raise ValueError("Case successes exceed runs")
        sum_runs += cr
        sum_successes += cs
    if successes > runs or (runs, successes) != (sum_runs, sum_successes):
        raise ValueError("Report counts are inconsistent with by_case")
    rate = successes / runs
    if "success_rate" in report and not math.isclose(_finite(report["success_rate"]), rate, abs_tol=1e-12):
        raise ValueError("Report success rate is inconsistent with counts")
    result["success_rate"] = rate
    reasons = report.get("failure_reasons", {})
    if not isinstance(reasons, dict) or any(not isinstance(k, str) for k in reasons):
        raise ValueError("Invalid failure reason mapping")
    for value in reasons.values():
        _count(value)
    if "failure_reasons" in report and sum(reasons.values()) != runs - successes:
        raise ValueError("Failure reason counts do not reconcile with successes/runs")
    result["failure_reasons"] = reasons
    duration = report.get("total_duration_seconds", report.get("duration_seconds"))
    if duration is not None:
        result["duration_seconds"] = _finite(duration)
    if any(k in report for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        tokens = {label: report.get(key) for label, key in
                  (("prompt", "prompt_tokens"), ("completion", "completion_tokens"), ("total", "total_tokens"))}
    else:
        tokens = report.get("tokens")
    if tokens is not None:
        if not isinstance(tokens, dict):
            raise ValueError("Invalid token metrics")
        if all(tokens.get(k) is not None for k in ("prompt", "completion", "total")):
            result["tokens"] = {k: _count(tokens[k]) for k in ("prompt", "completion", "total")}
        else:
            result.pop("tokens", None)  # missing is unknown, never zero
    if "provenance" in report and not isinstance(report["provenance"], dict):
        raise ValueError("Invalid report provenance")
    return result


def diff_eval_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two benchmark evaluation summaries and identify regressions and deltas."""
    before, after = _checked(before), _checked(after)
    before_sha = before.get("manifest_sha256", "")
    after_sha = after.get("manifest_sha256", "")
    manifest_mismatch = bool(before_sha and after_sha and before_sha != after_sha)

    b_runs = before.get("runs", 0)
    a_runs = after.get("runs", 0)
    b_successes = before.get("successes", 0)
    a_successes = after.get("successes", 0)
    b_rate = b_successes / b_runs
    a_rate = a_successes / a_runs

    summary: dict[str, Any] = {
        "runs": {"before": b_runs, "after": a_runs, "delta": a_runs - b_runs},
        "successes": {"before": b_successes, "after": a_successes, "delta": a_successes - b_successes},
        "success_rate": {"before": b_rate, "after": a_rate, "delta": float(Fraction(a_successes, a_runs) - Fraction(b_successes, b_runs))},
    }

    if "duration_seconds" in before and "duration_seconds" in after:
        b_dur = round(float(before.get("duration_seconds", 0.0)), 3)
        a_dur = round(float(after.get("duration_seconds", 0.0)), 3)
        summary["duration_seconds"] = {"before": b_dur, "after": a_dur, "delta": round(a_dur - b_dur, 3)}

    b_tok = before.get("tokens") or {}
    a_tok = after.get("tokens") or {}
    if b_tok and a_tok:
        summary["tokens"] = {
            "prompt": {"before": b_tok.get("prompt", 0), "after": a_tok.get("prompt", 0),
                       "delta": a_tok.get("prompt", 0) - b_tok.get("prompt", 0)},
            "completion": {"before": b_tok.get("completion", 0), "after": a_tok.get("completion", 0),
                           "delta": a_tok.get("completion", 0) - b_tok.get("completion", 0)},
            "total": {"before": b_tok.get("total", 0), "after": a_tok.get("total", 0),
                      "delta": a_tok.get("total", 0) - b_tok.get("total", 0)},
        }

    b_cases: dict[str, dict[str, int]] = before.get("by_case", {})
    a_cases: dict[str, dict[str, int]] = after.get("by_case", {})

    all_case_names = sorted(set(b_cases.keys()).union(a_cases.keys()))
    improved: list[dict[str, Any]] = []
    regressed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    for name in all_case_names:
        in_before = name in b_cases
        in_after = name in a_cases

        if in_before and not in_after:
            removed.append(name)
            continue
        if in_after and not in_before:
            added.append(name)
            continue

        b_succ = b_cases[name].get("successes", 0)
        b_r = b_cases[name].get("runs", 1)
        a_succ = a_cases[name].get("successes", 0)
        a_r = a_cases[name].get("runs", 1)

        delta = float(Fraction(a_succ, a_r) - Fraction(b_succ, b_r))

        if delta > 0:
            improved.append({
                "case": name,
                "before": f"{b_succ}/{b_r}",
                "after": f"{a_succ}/{a_r}",
                "delta_rate": delta,
            })
        elif delta < 0:
            regressed.append({
                "case": name,
                "before": f"{b_succ}/{b_r}",
                "after": f"{a_succ}/{a_r}",
                "delta_rate": delta,
            })
        else:
            unchanged.append(name)

    b_reasons: dict[str, int] = before.get("failure_reasons", {})
    a_reasons: dict[str, int] = after.get("failure_reasons", {})
    all_reasons = sorted(set(b_reasons.keys()).union(a_reasons.keys()))
    reasons_diff: dict[str, dict[str, int]] = {}
    for r in all_reasons:
        b_cnt = b_reasons.get(r, 0)
        a_cnt = a_reasons.get(r, 0)
        reasons_diff[r] = {
            "before": b_cnt,
            "after": a_cnt,
            "delta": a_cnt - b_cnt,
        }

    return {
        "version": 1,
        "manifest_mismatch": manifest_mismatch,
        "comparable": not manifest_mismatch,
        "comparison_scope": "Descriptive count differences, not causal or statistical proof",
        "provenance_mismatch": before.get("provenance") != after.get("provenance"),
        "summary": summary,
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "added": added,
        "removed": removed,
        "failure_reasons": reasons_diff,
    }


def format_eval_diff(diff: dict[str, Any], format: str = "markdown") -> str:
    """Format benchmark evaluation diff into markdown or plain text."""
    summary = diff["summary"]
    lines: list[str] = []

    if format == "markdown":
        lines.append("# Benchmark Run Comparison\n")
        if diff.get("manifest_mismatch"):
            lines.append("> [!WARNING] Manifest SHA-256 mismatch: runs were executed with different benchmark manifests.\n")

        lines.append("## Overall Summary\n")
        lines.append("| Metric | Before | After | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        lines.append(f"| Successes | {summary['successes']['before']} | {summary['successes']['after']} | {summary['successes']['delta']:+d} |")
        lines.append(f"| Total Runs | {summary['runs']['before']} | {summary['runs']['after']} | {summary['runs']['delta']:+d} |")

        b_pct = summary['success_rate']['before'] * 100
        a_pct = summary['success_rate']['after'] * 100
        d_pct = summary['success_rate']['delta'] * 100
        lines.append(f"| Success Rate | {b_pct:.1f}% | {a_pct:.1f}% | {d_pct:+.1f}% |")

        if "duration_seconds" in summary:
            lines.append(f"| Duration (s) | {summary['duration_seconds']['before']}s | {summary['duration_seconds']['after']}s | {summary['duration_seconds']['delta']:+.3f}s |")

        if "tokens" in summary:
            tok = summary["tokens"]
            lines.append(f"| Total Tokens | {tok['total']['before']} | {tok['total']['after']} | {tok['total']['delta']:+d} |")

        lines.append("\n## Case Changes\n")
        lines.append("| Case | Before | After | Delta Rate | Status |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for item in diff.get("improved", []):
            lines.append(f"| `{item['case']}` | {item['before']} | {item['after']} | {item['delta_rate'] * 100:+.1f}% | Improved |")
        for item in diff.get("regressed", []):
            lines.append(f"| `{item['case']}` | {item['before']} | {item['after']} | {item['delta_rate'] * 100:+.1f}% | **Regressed** |")
        for c in diff.get("added", []):
            lines.append(f"| `{c}` | - | present | - | Added |")
        for c in diff.get("removed", []):
            lines.append(f"| `{c}` | present | - | - | Removed |")
        for c in diff.get("unchanged", []):
            lines.append(f"| `{c}` | - | - | 0.0% | Unchanged |")

        if diff.get("failure_reasons"):
            lines.append("\n## Failure Reason Shifts\n")
            lines.append("| Failure Reason | Before | After | Delta |")
            lines.append("| --- | ---: | ---: | ---: |")
            for reason, counts in diff["failure_reasons"].items():
                lines.append(f"| `{reason}` | {counts['before']} | {counts['after']} | {counts['delta']:+d} |")

        return "\n".join(lines) + "\n"

    # Plain text format
    lines.append("=== Benchmark Comparison ===")
    if diff.get("manifest_mismatch"):
        lines.append("Warning: Manifest SHA-256 mismatch between runs.")
    b_pct = summary['success_rate']['before'] * 100
    a_pct = summary['success_rate']['after'] * 100
    d_pct = summary['success_rate']['delta'] * 100
    lines.append(f"Success Rate: {b_pct:.1f}% -> {a_pct:.1f}% ({d_pct:+.1f}%)")
    lines.append(f"Successes: {summary['successes']['before']} -> {summary['successes']['after']} ({summary['successes']['delta']:+d})")
    lines.append(f"Total Runs: {summary['runs']['before']} -> {summary['runs']['after']}")

    if diff.get("improved"):
        lines.append("\nImproved Cases:")
        for item in diff["improved"]:
            lines.append(f"  + {item['case']}: {item['before']} -> {item['after']} ({item['delta_rate'] * 100:+.1f}%)")
    if diff.get("regressed"):
        lines.append("\nRegressed Cases:")
        for item in diff["regressed"]:
            lines.append(f"  - {item['case']}: {item['before']} -> {item['after']} ({item['delta_rate'] * 100:+.1f}%)")
    if diff.get("failure_reasons"):
        lines.append("\nFailure Reasons Shift:")
        for reason, counts in diff["failure_reasons"].items():
            lines.append(f"  {reason}: {counts['before']} -> {counts['after']} ({counts['delta']:+d})")

    return "\n".join(lines) + "\n"

