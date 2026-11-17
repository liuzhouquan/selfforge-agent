"""Security, boundary validation, and formatting defense tests for benchmark diff (SF-BMDIFF-01)."""

import json
import os
from pathlib import Path

import pytest

from selfforge_agent.runtime.benchmark_diff import (
    diff_eval_reports,
    format_eval_diff,
    read_eval_report,
)


def _make_valid_report(
    *,
    manifest_sha: str = "a" * 64,
    by_case: dict[str, dict] | None = None,
    failure_reasons: dict[str, int] | None = None,
) -> dict:
    if by_case is None:
        by_case = {"case1": {"successes": 2, "runs": 2}}
    runs = sum(c["runs"] for c in by_case.values())
    successes = sum(c["successes"] for c in by_case.values())
    if failure_reasons is None:
        failure_reasons = {"err": runs - successes} if runs > successes else {}
    return {
        "version": 1,
        "manifest_sha256": manifest_sha,
        "runs": runs,
        "successes": successes,
        "success_rate": successes / runs if runs else 0.0,
        "by_case": by_case,
        "failure_reasons": failure_reasons,
    }


class TestReadEvalReportSecurity:
    """SF-BMDIFF-01-A1: Report file reading and input gating security."""

    def test_read_eval_report_rejects_non_path(self):
        for invalid in ["report.json", 12345, None, ["a.json"]]:
            with pytest.raises(TypeError, match="[Pp]ath"):
                read_eval_report(invalid)

    def test_read_eval_report_rejects_symlinked_parent_directory(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        real_file = real_dir / "report.json"
        real_file.write_text(json.dumps(_make_valid_report()), encoding="utf-8")

        sym_dir = tmp_path / "sym_dir"
        sym_dir.symlink_to(real_dir, target_is_directory=True)
        sym_file = sym_dir / "report.json"

        with pytest.raises(ValueError, match="[Ss]ymlink"):
            read_eval_report(sym_file)

    def test_read_eval_report_rejects_non_dict_json(self, tmp_path):
        for non_dict_content in ["42", '"a string"', "[1, 2, 3]", "true", "null"]:
            bad_file = tmp_path / "non_dict.json"
            bad_file.write_text(non_dict_content, encoding="utf-8")
            with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|object|dict"):
                read_eval_report(bad_file)

    def test_read_eval_report_rejects_null_byte_in_path(self):
        with pytest.raises(ValueError, match="null byte"):
            read_eval_report(Path("report\0.json"))

    def test_read_eval_report_rejects_fifo(self, tmp_path):
        fifo_path = tmp_path / "report_fifo.json"
        try:
            os.mkfifo(fifo_path)
        except (AttributeError, OSError):
            pytest.skip("FIFO not supported on this platform")

        with pytest.raises(ValueError, match="regular file|symlink"):
            read_eval_report(fifo_path)


class TestReportMetricsAndStructureSecurity:
    """SF-BMDIFF-01-A2: Structure validation and metrics integrity."""

    def test_checked_normalizes_manifest_sha(self):
        upper_sha = "A" * 64
        rep = _make_valid_report(manifest_sha=upper_sha)
        diff = diff_eval_reports(rep, rep)
        assert diff["manifest_mismatch"] is False

    def test_checked_rejects_null_byte_in_case_name(self):
        rep = _make_valid_report(by_case={"case\0evil": {"runs": 1, "successes": 1}})
        with pytest.raises(ValueError, match="null byte|[Ii]nvalid case"):
            diff_eval_reports(rep, rep)

    def test_checked_rejects_null_byte_in_failure_reason(self):
        rep = _make_valid_report(
            by_case={"case1": {"runs": 2, "successes": 1}},
            failure_reasons={"err\0null": 1},
        )
        with pytest.raises(ValueError, match="null byte|[Ii]nvalid failure"):
            diff_eval_reports(rep, rep)

    def test_checked_rejects_excessive_case_records(self):
        huge_cases = {f"case_{i}": {"runs": 1, "successes": 1} for i in range(10001)}
        rep = _make_valid_report(by_case=huge_cases)
        with pytest.raises(ValueError, match="bounded by_case"):
            diff_eval_reports(rep, rep)


class TestDiffAnalysisSecurity:
    """SF-BMDIFF-01-A3: Deterministic diff analysis and input safety."""

    def test_diff_eval_reports_rejects_non_dict_inputs(self):
        valid = _make_valid_report()
        for invalid in ["string", 12345, None, [valid]]:
            with pytest.raises(ValueError, match="report object"):
                diff_eval_reports(invalid, valid)
            with pytest.raises(ValueError, match="report object"):
                diff_eval_reports(valid, invalid)

    def test_diff_eval_reports_detects_provenance_mismatch(self):
        rep_a = {**_make_valid_report(), "provenance": {"commit": "a" * 40}}
        rep_b = {**_make_valid_report(), "provenance": {"commit": "b" * 40}}
        diff = diff_eval_reports(rep_a, rep_b)
        assert diff["provenance_mismatch"] is True


class TestFormattingDefenseSecurity:
    """SF-BMDIFF-01-A4: Formatting defense and output sanitization."""

    def test_format_eval_diff_rejects_invalid_diff_type_or_missing_summary(self):
        for invalid in ["not_a_diff", 12345, None, [], {}]:
            with pytest.raises((ValueError, TypeError)):
                format_eval_diff(invalid)

    def test_format_eval_diff_rejects_unsupported_format(self):
        valid_report = _make_valid_report()
        diff = diff_eval_reports(valid_report, valid_report)
        for unsupported in ["html", "xml", "csv", "json", "unknown", ""]:
            with pytest.raises(ValueError, match="[Uu]nsupported format"):
                format_eval_diff(diff, format=unsupported)

    def test_format_eval_diff_escapes_markdown_table_injection(self):
        rep_a = _make_valid_report(
            by_case={"case1 | injected | col": {"runs": 2, "successes": 1}},
            failure_reasons={"err | broken": 1},
        )
        rep_b = _make_valid_report(
            by_case={"case1 | injected | col": {"runs": 2, "successes": 2}},
        )
        diff = diff_eval_reports(rep_a, rep_b)
        md = format_eval_diff(diff, format="markdown")

        # The pipe inside the case name must be escaped or sanitized to prevent table column distortion
        for line in md.splitlines():
            if "Improved" in line:
                # Should not have raw unescaped pipe inside the case cell
                assert r"\|" in line or "injected" not in line or line.count("|") == 6


@pytest.mark.parametrize('field', ['runs', 'successes', 'duration_seconds', 'tokens'])
def test_formatter_rejects_injected_numeric_summary_fields(field):
    report = _make_valid_report()
    diff = diff_eval_reports(report, report)
    bad = '1|x\nFORGED'
    if field == 'tokens':
        diff['summary'][field] = {'total': {'before': bad, 'after': 1, 'delta': 0}}
    elif field == 'duration_seconds':
        diff['summary'][field] = {'before': bad, 'after': 1, 'delta': 0}
    else:
        diff['summary'][field]['before'] = bad
    with pytest.raises(ValueError):
        format_eval_diff(diff)


def test_formatter_rejects_injected_case_ratio():
    report = _make_valid_report()
    diff = diff_eval_reports(report, report)
    diff['improved'] = [{'case': 'fixture', 'before': '1/2|\nFORGED', 'after': '2/2', 'delta_rate': 0.5}]
    with pytest.raises(ValueError):
        format_eval_diff(diff)


@pytest.mark.parametrize('value', [True, float('nan'), float('inf')])
def test_formatter_rejects_non_numeric_or_nonfinite_deltas(value):
    report = _make_valid_report()
    diff = diff_eval_reports(report, report)
    diff['summary']['success_rate']['delta'] = value
    with pytest.raises(ValueError):
        format_eval_diff(diff)
