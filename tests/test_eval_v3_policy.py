"""Tests for frozen v3 22-task evaluation specification, policy guardrails, and preflight (SF-EVAL-08)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from selfforge_agent import cli
from selfforge_agent.runtime.benchmark import FAILURE_REASON_TAXONOMY, validate_suite
from selfforge_agent.runtime.eval_policy import verify_eval_policy_bundle

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "benchmark" / "validated-candidates"
PREPARE_SCRIPT = ROOT / "scripts" / "prepare_eval_sources.py"

HISTORICAL_V1_MANIFEST_SHA256 = (
    "0a5bd935e7d0aa4b6b9a93ad101686a50c57af2cd1b4143accd5a3bc4e327fd8"
)
HISTORICAL_V2_MANIFEST_SHA256 = (
    "3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6"
)
HISTORICAL_V2_ASSETS_SHA256 = (
    "89bd73aaa068193257a7e5392632f2eedd831e3c9723cdf8ca5bd8fee001795d"
)
HISTORICAL_BASE_IMAGE_ID = (
    "sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20"
)
HISTORICAL_ITSDANGEROUS_IMAGE_ID = (
    "sha256:205fa9f663b395ace68468c42cfdfdb6c0b180593504c67d913634f962860da6"
)


def _load_prepare_module():
    spec = importlib.util.spec_from_file_location("prepare_eval_sources", PREPARE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prepare_eval_sources"] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _create_mini_bundle(base: Path) -> tuple[Path, Path, Path, Path]:
    """Create a self-contained valid mini policy bundle in tmp_path for negative testing."""
    root = base / "bundle"
    root.mkdir()
    holdouts = root / "holdouts"
    refs = root / "references"
    holdouts.mkdir()
    refs.mkdir()

    holdout_file = holdouts / "test_demo.py"
    holdout_file.write_text("def test_holdout():\n    assert True\n", encoding="utf-8")
    patch_file = refs / "demo.patch"
    patch_file.write_text("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n", encoding="utf-8")

    lockfile = root / "demo.lock"
    lockfile.write_text("pytest==8.3.5 --hash=sha256:" + ("a" * 64) + "\n", encoding="utf-8")

    v2_manifest = root / "v2.json"
    v2_manifest.write_bytes(b'{"version": 1, "cases": []}\n')
    v2_assets = root / "v2.assets.json"
    v2_assets.write_bytes(b"{}\n")

    manifest_file = root / "suite-v3.json"
    manifest_payload = {
        "version": 1,
        "cases": [
            {
                "id": "demo_case",
                "repository": "../../.local/benchmark/repos/demo",
                "revision": "a" * 40,
                "task": "Fix demo issue cleanly",
                "allowed_changes": ["pkg/mod.py", "tests/test_mod.py"],
                "holdout_test": "holdouts/test_demo.py",
                "agent_test_path": "tests/test_mod.py",
                "reference_patch": "references/demo.patch",
                "image": "selfforge-agent-sandbox:latest",
            }
        ],
    }
    manifest_file.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")

    assets_file = root / "suite-v3.assets.json"
    assets_payload = {
        "holdouts/test_demo.py": hashlib.sha256(holdout_file.read_bytes()).hexdigest(),
        "references/demo.patch": hashlib.sha256(patch_file.read_bytes()).hexdigest(),
    }
    assets_file.write_text(json.dumps(assets_payload, indent=2) + "\n", encoding="utf-8")

    policy_file = root / "suite-v3.policy.json"
    policy_payload = {
        "policy_version": 3,
        "suite_id": "mini-v3",
        "manifest": "suite-v3.json",
        "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        "assets_inventory": "suite-v3.assets.json",
        "assets_inventory_sha256": hashlib.sha256(assets_file.read_bytes()).hexdigest(),
        "historical_baseline": {
            "manifest": "v2.json",
            "manifest_sha256": hashlib.sha256(v2_manifest.read_bytes()).hexdigest(),
            "assets_inventory": "v2.assets.json",
            "assets_inventory_sha256": hashlib.sha256(v2_assets.read_bytes()).hexdigest(),
            "runs": 60,
            "successes": 53,
            "separate_from_new_policy": True,
        },
        "repositories": {
            "demo": {
                "url": "https://github.com/example/demo.git",
                "revision": "a" * 40,
            }
        },
        "environment": {
            "sandbox": "docker",
            "network_mode": "none",
            "image_policy": "resolve_fresh_local_build",
            "images": {
                "default": {
                    "reference": "selfforge-agent-sandbox:latest",
                    "dockerfile": "docker/Dockerfile",
                    "cases": 1,
                    "historical_digest_distinct_from": HISTORICAL_BASE_IMAGE_ID,
                }
            },
            "lockfiles": {
                "demo": {
                    "path": "demo.lock",
                    "sha256": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
                }
            },
        },
        "provider_policy": {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "fallback": False,
            "require_effective_config_check": True,
        },
        "budgets": {
            "repeats": 3,
            "total_planned_runs": 3,
            "max_steps": 18,
            "time_budget_seconds": 180,
            "test_timeout_seconds": 30,
            "context_budget_tokens": 8000,
        },
        "ceilings": {
            "max_case_attempts": 3,
            "max_model_calls_upper_bound": 54,
            "max_wall_clock_minutes": 30,
            "max_spend_usd": 5.0,
            "require_rate_card_for_spend_estimate": True,
            "allow_auto_rerun": False,
        },
        "failure_taxonomy": {
            "success_reason": "passed",
            "failure_reasons": list(FAILURE_REASON_TAXONOMY),
            "agent_error_codes": [
                "provider_http_<status>",
                "provider_unavailable",
                "model_finish_reason",
                "runtime_error",
                "unknown",
            ],
        },
        "execution_status": {
            "model_benchmark_executed": False,
            "completed_runs": 0,
            "successes": None,
            "success_rate": None,
            "score_status": "pending_live_execution",
        },
    }
    policy_file.write_text(json.dumps(policy_payload, indent=2) + "\n", encoding="utf-8")
    return policy_file, manifest_file, assets_file, holdout_file


def test_v3_policy_bundle_integrity_and_historical_v2_preservation() -> None:
    """SF-EVAL-08-A1 & A3: v3 22-task spec, assets, ceilings, and frozen historical files."""
    policy_path = CANDIDATES / "suite-twenty-two-tasks-v3.policy.json"
    bundle = verify_eval_policy_bundle(policy_path)

    assert bundle.policy.policy_version == 3
    assert bundle.policy.suite_id == "twenty-two-tasks-v3"
    assert len(bundle.suite.cases) == 22
    assert len({c.id for c in bundle.suite.cases}) == 22
    assert set(bundle.policy.repositories) == {
        "pocketflow",
        "boltons",
        "more-itertools",
        "itsdangerous",
        "jmespath",
    }

    v2_manifest = CANDIDATES / "suite-twenty-tasks-v2.json"
    v2_assets = CANDIDATES / "suite-twenty-tasks-v2.assets.json"
    v1_manifest = CANDIDATES / "suite-twenty-tasks.json"
    assert hashlib.sha256(v1_manifest.read_bytes()).hexdigest() == HISTORICAL_V1_MANIFEST_SHA256
    assert hashlib.sha256(v2_manifest.read_bytes()).hexdigest() == HISTORICAL_V2_MANIFEST_SHA256
    assert hashlib.sha256(v2_assets.read_bytes()).hexdigest() == HISTORICAL_V2_ASSETS_SHA256

    v2_data = json.loads(v2_manifest.read_text(encoding="utf-8"))
    v2_ids = {case["id"] for case in v2_data["cases"]}
    v3_ids = {case.id for case in bundle.suite.cases}
    assert v2_ids.issubset(v3_ids)
    assert "jmespath_unknown_function_suggestions" in v3_ids
    assert "boltons_remap_mappings" in v3_ids

    assert bundle.manifest_sha256 != HISTORICAL_V2_MANIFEST_SHA256
    assert bundle.assets_inventory_sha256 != HISTORICAL_V2_ASSETS_SHA256

    for case in bundle.suite.cases:
        assert case.reference_patch is not None
        assert case.image in {"selfforge-agent-sandbox:latest", "selfforge-itsdangerous:locked"}
        assert case.image not in {HISTORICAL_BASE_IMAGE_ID, HISTORICAL_ITSDANGEROUS_IMAGE_ID}

    assert bundle.policy.provider_policy.provider == "deepseek"
    assert bundle.policy.provider_policy.model == "deepseek-flash"
    assert bundle.policy.provider_policy.base_url == "https://api.deepseek.com"
    assert bundle.policy.provider_policy.fallback is False

    assert bundle.policy.budgets.repeats == 3
    assert bundle.policy.budgets.total_planned_runs == 66
    assert bundle.policy.budgets.max_steps == 18
    assert bundle.policy.budgets.time_budget_seconds == 180
    assert bundle.policy.budgets.test_timeout_seconds == 30
    assert bundle.policy.budgets.context_budget_tokens == 8000

    assert bundle.policy.ceilings.max_case_attempts == 66
    assert bundle.policy.ceilings.max_model_calls_upper_bound == 1188
    assert bundle.policy.ceilings.max_wall_clock_minutes > 0
    assert bundle.policy.ceilings.max_spend_usd > 0
    assert bundle.policy.ceilings.allow_auto_rerun is False
    assert set(bundle.policy.failure_taxonomy.failure_reasons) == set(FAILURE_REASON_TAXONOMY)

    # SF-EVAL-08-A3: no live score claimed before actual live execution
    assert bundle.policy.execution_status.model_benchmark_executed is False
    assert bundle.policy.execution_status.completed_runs == 0
    assert bundle.policy.execution_status.successes is None
    assert bundle.policy.execution_status.success_rate is None
    assert bundle.policy.execution_status.score_status == "pending_live_execution"


def test_v3_policy_normalization_and_fail_closed_rejections(tmp_path: Path) -> None:
    """Reject symlinks, tampered digests, incomplete taxonomy, ceiling mismatch, and fake scores."""
    policy_file, manifest_file, _assets_file, holdout_file = _create_mini_bundle(tmp_path)

    # Uppercase valid SHA-256 hex normalizes cleanly to lowercase at ingestion
    raw = json.loads(policy_file.read_text(encoding="utf-8"))
    raw["manifest_sha256"] = raw["manifest_sha256"].upper()
    policy_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    normalized_bundle = verify_eval_policy_bundle(policy_file)
    assert normalized_bundle.manifest_sha256 == normalized_bundle.policy.manifest_sha256.lower()

    # 1. Incomplete failure taxonomy is rejected
    bad_taxonomy = json.loads(policy_file.read_text(encoding="utf-8"))
    bad_taxonomy["failure_taxonomy"]["failure_reasons"].remove("agent_timed_out")
    policy_file.write_text(json.dumps(bad_taxonomy), encoding="utf-8")
    with pytest.raises(ValueError, match="failure_reasons"):
        verify_eval_policy_bundle(policy_file)

    # 2. Fake model score without execution is rejected
    bad_score = dict(raw)
    bad_score["execution_status"] = {
        "model_benchmark_executed": False,
        "completed_runs": 0,
        "successes": 3,
        "success_rate": 1.0,
        "score_status": "pending_live_execution",
    }
    policy_file.write_text(json.dumps(bad_score), encoding="utf-8")
    with pytest.raises(ValueError, match="execution_status|success"):
        verify_eval_policy_bundle(policy_file)

    # 3. Call-ceiling mismatch is rejected
    bad_ceiling = dict(raw)
    bad_ceiling["ceilings"] = {**raw["ceilings"], "max_model_calls_upper_bound": 999}
    policy_file.write_text(json.dumps(bad_ceiling), encoding="utf-8")
    with pytest.raises(ValueError, match="max_model_calls_upper_bound"):
        verify_eval_policy_bundle(policy_file)

    # Restore clean policy and tamper an asset hash
    policy_file.write_text(json.dumps(raw), encoding="utf-8")
    holdout_file.write_text("def test_holdout():\n    assert False\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest|SHA-256"):
        verify_eval_policy_bundle(policy_file)

    # Symlink policy file is rejected fail-closed
    symlink_policy = tmp_path / "symlink.policy.json"
    symlink_policy.symlink_to(policy_file)
    with pytest.raises(ValueError, match="symlink"):
        verify_eval_policy_bundle(symlink_policy)

    # Non-regular / tampered assets symlink is rejected
    manifest_file.unlink()
    manifest_file.symlink_to(policy_file)
    with pytest.raises(ValueError, match="symlink"):
        verify_eval_policy_bundle(policy_file)


def test_eval_validate_v3_model_free_cli_and_source_shas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SF-EVAL-08-A2: eval-validate verifies baseline failure + reference pass without any model."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "from pkg import add\n\ndef test_same():\n    assert add(3, 3) == 0\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "buggy baseline")
    revision = _git(repo, "rev-parse", "HEAD")

    holdout = tmp_path / "test_holdout.py"
    holdout.write_text(
        "from pkg import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    patch = tmp_path / "fix.patch"
    patch.write_text(
        "diff --git a/pkg/__init__.py b/pkg/__init__.py\n"
        "--- a/pkg/__init__.py\n"
        "+++ b/pkg/__init__.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
        "diff --git a/tests/test_pkg.py b/tests/test_pkg.py\n"
        "--- a/tests/test_pkg.py\n"
        "+++ b/tests/test_pkg.py\n"
        "@@ -1,4 +1,5 @@\n"
        " from pkg import add\n"
        " \n"
        " def test_same():\n"
        "-    assert add(3, 3) == 0\n"
        "+    assert add(3, 3) == 6\n"
        "+    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({
            "version": 1,
            "cases": [{
                "id": "demo_math_fix",
                "repository": str(repo),
                "revision": revision,
                "task": "Fix add and update regression tests",
                "allowed_changes": ["pkg/__init__.py", "tests/test_pkg.py"],
                "holdout_test": str(holdout),
                "agent_test_path": "tests/test_pkg.py",
                "reference_patch": str(patch),
            }],
        }),
        encoding="utf-8",
    )
    report_out = tmp_path / "report.json"

    def _forbid(*_args, **_kwargs):
        raise AssertionError("Provider/model/HTTP must never be constructed during eval-validate")

    monkeypatch.setattr("selfforge_agent.providers.route_providers", _forbid)
    monkeypatch.setattr("selfforge_agent.integrations.model_http.request_json", _forbid)

    exit_code = cli.main([
        "eval-validate",
        "--manifest",
        str(manifest),
        "--sandbox",
        "host",
        "--trust-local",
        "--output",
        str(report_out),
    ])
    capsys.readouterr()
    assert exit_code == 0

    report = json.loads(report_out.read_text(encoding="utf-8"))
    assert report["status"] == "validated"
    assert report["cases"] == 1
    assert report["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    case = report["baselines"]["demo_math_fix"]
    assert case["revision"] == revision
    assert case["status"] == "failed"
    assert case["failed"] == 1
    assert case["holdout_sha256"] == hashlib.sha256(holdout.read_bytes()).hexdigest()
    assert case["reference"]["status"] == "validated"
    assert case["reference"]["patch_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert case["reference"]["visible"]["status"] == "passed"
    assert case["reference"]["holdout"]["status"] == "passed"


def test_prepare_eval_sources_v3_profile_covers_all_five_repos(tmp_path: Path) -> None:
    """SF-EVAL-08-A2: prepare_eval_sources --profile v3 registers and validates all 5 repos."""
    mod = _load_prepare_module()
    policy_data = json.loads(
        (CANDIDATES / "suite-twenty-two-tasks-v3.policy.json").read_text(encoding="utf-8")
    )
    by_name = {item.name: item for item in mod.get_profile_sources("v3")}
    assert set(by_name) == set(policy_data["repositories"])
    for repo_name, expected in policy_data["repositories"].items():
        assert by_name[repo_name].url == expected["url"]
        assert by_name[repo_name].revision == expected["revision"]

    # Local fake-remote end-to-end profile upgrade from candidates -> v3 -> offline
    cache_root = tmp_path / "cache"
    fake_sources = []
    for name in ("jmespath", "boltons", "pocketflow", "more-itertools", "itsdangerous"):
        remote = tmp_path / f"remote-{name}"
        remote.mkdir()
        _git(remote, "init", "-q")
        _git(remote, "config", "user.name", "Fixture")
        _git(remote, "config", "user.email", "fixture@example.invalid")
        (remote / "mod.py").write_text(f"NAME = {name!r}\n", encoding="utf-8")
        _git(remote, "add", ".")
        _git(remote, "commit", "-qm", f"{name} commit")
        rev = _git(remote, "rev-parse", "HEAD")
        fake_sources.append(mod.PinnedEvalSource(name=name, url=str(remote), revision=rev))

    candidates_only = fake_sources[:2]
    res_small = mod.prepare_sources(cache_root, sources=candidates_only)
    assert set(res_small) == {"jmespath", "boltons"}

    res_all = mod.prepare_sources(cache_root, sources=fake_sources)
    assert set(res_all) == {"jmespath", "boltons", "pocketflow", "more-itertools", "itsdangerous"}

    res_offline = mod.prepare_sources(cache_root, offline=True, sources=fake_sources)
    assert res_offline == res_all

    with pytest.raises(mod.PreparationError, match="profile|Unknown"):
        mod.prepare_sources(cache_root, profile="nonexistent")


@pytest.mark.integration
def test_v3_suite_docker_reference_validation(tmp_path: Path) -> None:
    """SF-EVAL-08-A2: Real offline Docker eval-validate across all 22 v3 tasks without models."""
    manifest_path = CANDIDATES / "suite-twenty-two-tasks-v3.json"
    policy_path = CANDIDATES / "suite-twenty-two-tasks-v3.policy.json"
    bundle = verify_eval_policy_bundle(policy_path)

    for probe in (
        ["docker", "info"],
        ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"],
        ["docker", "image", "inspect", "selfforge-itsdangerous:locked"],
    ):
        try:
            proc = subprocess.run(probe, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Docker environment unavailable: {exc}")
        if proc.returncode != 0:
            pytest.skip(f"Required Docker resource unavailable ({' '.join(probe)})")

    source_root = Path(
        os.environ.get("SELFFORGE_EVAL_SOURCE_ROOT", ROOT / ".local" / "benchmark" / "repos")
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        repo_dir = source_root / Path(case["repository"]).name
        if not repo_dir.is_dir():
            pytest.fail(
                "Provision all 5 v3 pinned sources first with: "
                "python scripts/prepare_eval_sources.py --profile v3"
            )
        case["repository"] = str(repo_dir.resolve())
        for field in ("holdout_test", "reference_patch"):
            case[field] = str((manifest_path.parent / case[field]).resolve())

    resolved_manifest = tmp_path / manifest_path.name
    resolved_manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = validate_suite(resolved_manifest, sandbox="docker", test_timeout=30)
    report_bytes = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    report_path = tmp_path / "twenty-two-v3-preflight.json"
    report_path.write_bytes(report_bytes)
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()

    assert report["status"] == "validated"
    assert report["cases"] == 22
    assert report["sandbox"] == "docker"
    assert set(report["images"]) == {case.id for case in bundle.suite.cases}
    for case in bundle.suite.cases:
        img = report["images"][case.id]
        assert img["reference"] == case.image
        assert img["id"].startswith("sha256:") and len(img["id"]) == 71

        baseline = report["baselines"][case.id]
        assert baseline["revision"] == case.revision
        assert baseline["status"] == "failed"
        assert baseline["failed"] >= 1
        ref = baseline["reference"]
        assert ref["status"] == "validated"
        assert ref["visible"]["status"] == "passed"
        assert ref["visible"]["passed"] >= 1
        assert ref["visible"]["failed"] == 0
        assert ref["visible"]["errors"] == 0
        assert ref["holdout"]["status"] == "passed"
        assert ref["holdout"]["passed"] >= 1
        assert ref["holdout"]["failed"] == 0
        assert ref["holdout"]["errors"] == 0

    print(
        json.dumps(
            {
                "frozen_manifest_sha256": bundle.manifest_sha256,
                "frozen_assets_sha256": bundle.assets_inventory_sha256,
                "report_sha256": report_sha256,
                "resolved_images": sorted(
                    {(v["reference"], v["id"]) for v in report["images"].values()}
                ),
            },
            indent=2,
        )
    )


def test_policy_rejects_symlink_ancestor_before_resolution(tmp_path: Path) -> None:
    policy, *_ = _create_mini_bundle(tmp_path)
    alias = tmp_path / 'policy-alias'
    alias.symlink_to(policy.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        verify_eval_policy_bundle(alias / policy.name)


@pytest.mark.parametrize('field', ['time_budget_seconds', 'test_timeout_seconds'])
def test_policy_rejects_infinite_budgets(tmp_path: Path, field: str) -> None:
    policy, *_ = _create_mini_bundle(tmp_path)
    data = json.loads(policy.read_text())
    data['budgets'][field] = float('inf')
    policy.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify_eval_policy_bundle(policy)


def test_policy_checks_image_profile_lockfile_digest(tmp_path: Path) -> None:
    policy, *_ = _create_mini_bundle(tmp_path)
    data = json.loads(policy.read_text())
    profile = data['environment']['images']['default']
    profile['lockfile'] = 'demo.lock'
    profile['lockfile_sha256'] = '0' * 64
    policy.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='[Ll]ockfile'):
        verify_eval_policy_bundle(policy)


@pytest.mark.parametrize('changes', [
    {'successes': 4}, {'success_rate': 0.2}, {'score_status': 'pending_live_execution'},
    {'completed_runs': 4, 'successes': 4, 'success_rate': 1.0},
])
def test_policy_rejects_inconsistent_executed_scores(tmp_path: Path, changes: dict) -> None:
    policy, *_ = _create_mini_bundle(tmp_path)
    data = json.loads(policy.read_text())
    data['execution_status'] = {
        'model_benchmark_executed': True, 'completed_runs': 3,
        'successes': 3, 'success_rate': 1.0, 'score_status': 'completed', **changes,
    }
    policy.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify_eval_policy_bundle(policy)
