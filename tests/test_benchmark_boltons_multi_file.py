"""Tests for boltons multi-file real-repository candidate task qualification (SF-EVAL-07)."""

import hashlib
from pathlib import Path

from selfforge_agent.runtime.benchmark import load_suite, validate_suite

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "benchmark" / "validated-candidates"


def test_boltons_multi_file_manifest_structural_integrity() -> None:
    manifest_path = CANDIDATES / "suite-boltons-remap-mappings.json"
    assert manifest_path.is_file()

    suite, prepared = load_suite(manifest_path)
    assert len(suite.cases) == 1
    assert len(prepared) == 1
    case = suite.cases[0]

    assert case.id == "boltons_remap_mappings"
    assert len(case.revision) == 40
    assert len(case.allowed_changes) >= 4

    # Allowed changes must span multiple distinct source modules plus tests
    source_changes = [p for p in case.allowed_changes if not p.startswith("tests/")]
    assert len(source_changes) >= 2
    assert "boltons/dictutils.py" in source_changes
    assert "boltons/iterutils.py" in source_changes

    holdout_path = (CANDIDATES / case.holdout_test).resolve()
    assert holdout_path.is_file()

    patch_path = (CANDIDATES / case.reference_patch).resolve()
    assert patch_path.is_file()

    # Verify patch diff touches multiple distinct source and test files
    patch_content = patch_path.read_text(encoding="utf-8")
    diff_targets = [
        line.split(" b/", 1)[1].strip()
        for line in patch_content.splitlines()
        if line.startswith("diff --git a/")
    ]
    assert len(diff_targets) >= 4
    assert set(diff_targets).issubset(set(case.allowed_changes))
    assert "boltons/dictutils.py" in diff_targets
    assert "boltons/iterutils.py" in diff_targets


def test_boltons_multi_file_task_reference_validation() -> None:
    manifest_path = CANDIDATES / "suite-boltons-remap-mappings.json"
    report = validate_suite(manifest_path, sandbox="host", trusted_host=True)

    assert report["status"] == "validated"
    assert report["cases"] == 1

    case_report = report["baselines"]["boltons_remap_mappings"]
    # Negative control: baseline must fail by assertion without collection errors
    assert case_report["status"] == "failed"
    assert case_report["failed"] >= 1

    # Positive control: reference patch passes both visible and holdout
    ref = case_report["reference"]
    assert ref["status"] == "validated"
    assert ref["visible"]["status"] == "passed"
    assert ref["visible"]["passed"] >= 1
    assert ref["visible"]["failed"] == 0
    assert ref["holdout"]["status"] == "passed"
    assert ref["holdout"]["passed"] >= 1
    assert ref["holdout"]["failed"] == 0

    # Must have changed multiple source files
    changed_sources = [f for f in ref["changed_files"] if not f.startswith("tests/")]
    assert len(changed_sources) >= 2


def test_historical_v2_suite_and_assets_remain_unmodified() -> None:
    v2_manifest = CANDIDATES / "suite-twenty-tasks-v2.json"
    v2_assets = CANDIDATES / "suite-twenty-tasks-v2.assets.json"

    assert hashlib.sha256(v2_manifest.read_bytes()).hexdigest() == (
        "3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6"
    )
    assert hashlib.sha256(v2_assets.read_bytes()).hexdigest() == (
        "89bd73aaa068193257a7e5392632f2eedd831e3c9723cdf8ca5bd8fee001795d"
    )

