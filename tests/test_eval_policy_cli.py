"""Tests for SF-EVAL-09 CLI --policy integration, budget gates, and safe exports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfforge_agent.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V3_POLICY_PATH = PROJECT_ROOT / "benchmark" / "validated-candidates" / "suite-twenty-two-tasks-v3.policy.json"


def _make_dummy_eval_report() -> dict:
    return {
        "manifest": "dummy-manifest",
        "manifest_sha256": "0" * 64,
        "runs": 1,
        "successes": 1,
        "tasks": 1,
        "results": [
            {
                "case_id": "c1",
                "attempt": 1,
                "passed": True,
                "reason": "passed",
                "duration_seconds": 1.0,
            }
        ],
        "by_case": {
            "c1": {
                "runs": 1,
                "successes": 1,
            }
        },
    }


def _make_dummy_validate_report() -> dict:
    return {
        "manifest": "dummy-manifest",
        "manifest_sha256": "0" * 64,
        "tasks": 1,
        "successes": 1,
        "results": [
            {
                "case_id": "c1",
                "valid": True,
                "reason": "passed",
            }
        ],
    }


def test_eval_validate_policy_flag_validation_and_provenance(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # 1. Neither --manifest nor --policy
    with pytest.raises(SystemExit) as exc:
        main(["eval-validate"])
    assert exc.value.code == 2

    # 2. Both --manifest and --policy are mutually exclusive
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["eval-validate", "--manifest", str(manifest_path), "--policy", str(V3_POLICY_PATH)])
    assert exc.value.code == 2

    # 3. Invalid/tampered policy fails closed without calling validate_suite
    bad_policy = tmp_path / "bad.policy.json"
    bad_policy.write_text('{"policy_version": 999}', encoding="utf-8")
    with patch("selfforge_agent.cli.validate_suite") as mock_validate:
        with pytest.raises(SystemExit) as exc:
            main(["eval-validate", "--policy", str(bad_policy)])
        assert exc.value.code == 2
        mock_validate.assert_not_called()

    # 4. Valid policy runs validate_suite and injects policy provenance
    fake_report = _make_dummy_validate_report()
    with patch("selfforge_agent.cli.validate_suite", return_value=fake_report) as mock_validate:
        exit_code = main(["eval-validate", "--policy", str(V3_POLICY_PATH)])
        assert exit_code == 0
        assert mock_validate.called
        passed_manifest = mock_validate.call_args[0][0]
        assert str(passed_manifest).endswith("suite-twenty-two-tasks-v3.json")

    stdout = capsys.readouterr().out
    rendered = json.loads(stdout)
    assert "policy" in rendered
    policy_meta = rendered["policy"]
    assert policy_meta["suite_id"] == "twenty-two-tasks-v3"
    assert policy_meta["policy_version"] == 3
    assert len(policy_meta["policy_sha256"]) == 64
    assert len(policy_meta["manifest_sha256"]) == 64
    assert len(policy_meta["assets_inventory_sha256"]) == 64


def test_eval_suite_policy_enforcement_and_conflicts(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # 1. Neither --manifest nor --policy
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite"])
    assert exc.value.code == 2

    # 2. Both --manifest and --policy
    dummy_manifest = tmp_path / "dummy.json"
    dummy_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite", "--manifest", str(dummy_manifest), "--policy", str(V3_POLICY_PATH)])
    assert exc.value.code == 2

    # 3. Conflict on sandbox: policy requires docker, CLI passes host
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--sandbox", "host", "--trust-local"])
    assert exc.value.code == 2

    # 4. Conflict on repeats: policy specifies 3, CLI passes 1
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--repeats", "1"])
    assert exc.value.code == 2

    # 5. Conflict on max-steps: policy specifies 18, CLI passes 10
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--max-steps", "10"])
    assert exc.value.code == 2

    # 6. Conflict on provider: policy specifies deepseek, CLI passes openrouter
    with pytest.raises(SystemExit) as exc:
        main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--provider", "openrouter"])
    assert exc.value.code == 2

    # 7. Valid policy run with defaults taken from policy bundle
    fake_report = _make_dummy_eval_report()
    with (
        patch("selfforge_agent.cli.evaluate_suite", return_value=fake_report) as mock_eval,
        patch("selfforge_agent.cli.route_providers") as mock_route,
    ):
        mock_route.return_value = [MagicMock()]
        exit_code = main(["eval-suite", "--policy", str(V3_POLICY_PATH)])
        assert exit_code == 0
        assert mock_eval.called
        kwargs = mock_eval.call_args[1]
        assert kwargs["repeats"] == 3
        assert kwargs["max_steps"] == 18
        assert kwargs["time_budget"] == 180.0
        assert kwargs["test_timeout"] == 30.0
        assert kwargs["sandbox"] == "docker"

    stdout = capsys.readouterr().out
    rendered = json.loads(stdout)
    assert "policy" in rendered
    assert rendered["policy"]["suite_id"] == "twenty-two-tasks-v3"


def test_eval_cli_output_guards_across_eval_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Setup test targets: symlink, dir, missing parent, and FIFO
    target_real = tmp_path / "real_file.txt"
    target_real.write_text("DO_NOT_OVERWRITE", encoding="utf-8")
    symlink_out = tmp_path / "symlink.json"
    symlink_out.symlink_to(target_real)

    dir_out = tmp_path / "target_dir"
    dir_out.mkdir()

    missing_parent_out = tmp_path / "nonexistent_sub" / "output.json"

    fifo_out = tmp_path / "output.fifo"
    os.mkfifo(fifo_out)

    dummy_validate = _make_dummy_validate_report()
    dummy_eval = _make_dummy_eval_report()

    eval_before = tmp_path / "before.json"
    eval_after = tmp_path / "after.json"
    eval_before.write_text(json.dumps(dummy_eval), encoding="utf-8")
    eval_after.write_text(json.dumps(dummy_eval), encoding="utf-8")

    bad_targets = (symlink_out, dir_out, missing_parent_out, fifo_out)

    for bad_path in bad_targets:
        # eval-validate --output
        with patch("selfforge_agent.cli.validate_suite", return_value=dummy_validate):
            with pytest.raises(SystemExit) as exc:
                main(["eval-validate", "--policy", str(V3_POLICY_PATH), "--output", str(bad_path)])
            assert exc.value.code == 2

        # eval-suite --output
        with (
            patch("selfforge_agent.cli.evaluate_suite", return_value=dummy_eval),
            patch("selfforge_agent.cli.route_providers", return_value=[MagicMock()]),
            pytest.raises(SystemExit) as exc,
        ):
            main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--output", str(bad_path)])
        assert exc.value.code == 2

        # eval-diff --output
        with pytest.raises(SystemExit) as exc:
            main(["eval-diff", str(eval_before), str(eval_after), "--output", str(bad_path)])
        assert exc.value.code == 2

    # Target file was never overwritten
    assert target_real.read_text(encoding="utf-8") == "DO_NOT_OVERWRITE"

    # Valid regular outputs write correctly
    valid_validate_out = tmp_path / "valid_validate.json"
    with patch("selfforge_agent.cli.validate_suite", return_value=dummy_validate):
        code = main(["eval-validate", "--policy", str(V3_POLICY_PATH), "--output", str(valid_validate_out)])
        assert code == 0
        assert valid_validate_out.is_file()
        assert json.loads(valid_validate_out.read_text(encoding="utf-8"))["policy"]["suite_id"] == "twenty-two-tasks-v3"

    valid_suite_out = tmp_path / "valid_suite.json"
    with (
        patch("selfforge_agent.cli.evaluate_suite", return_value=dummy_eval),
        patch("selfforge_agent.cli.route_providers", return_value=[MagicMock()]),
    ):
        code = main(["eval-suite", "--policy", str(V3_POLICY_PATH), "--output", str(valid_suite_out)])
        assert code == 0
        assert valid_suite_out.is_file()
        assert json.loads(valid_suite_out.read_text(encoding="utf-8"))["policy"]["suite_id"] == "twenty-two-tasks-v3"

    valid_diff_out = tmp_path / "valid_diff.json"
    code = main(["eval-diff", str(eval_before), str(eval_after), "--format", "json", "--output", str(valid_diff_out)])
    assert code == 0
    assert valid_diff_out.is_file()
    assert "summary" in json.loads(valid_diff_out.read_text(encoding="utf-8"))
