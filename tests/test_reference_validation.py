"""Positive reference controls must qualify tasks before model evaluation."""

import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_benchmark import ScriptedModel, _git, _manifest

from selfforge_agent import cli
from selfforge_agent.runtime import benchmark
from selfforge_agent.runtime.benchmark import evaluate_suite, load_suite, validate_suite
from selfforge_agent.runtime.model import ModelTurn


def patch(old: str, new: str, path: str = "app.py") -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       fromfile="a/" + path, tofile="b/" + path))


def reference_suite(root: Path, *, both=False):
    manifest, repos = _manifest(root)
    data = json.loads(manifest.read_text())
    if not both:
        data["cases"] = data["cases"][:1]
    for case, repo in zip(data["cases"], repos):
        old = (repo / "app.py").read_text()
        new = old.replace("return a - b", "return a + b").replace(
            "return name.upper()", "return name.strip().lower()")
        new += "# PRIVATE_REFERENCE_SENTINEL\n"
        path = root / (case["id"] + ".patch")
        path.write_text(patch(old, new))
        case["reference_patch"] = path.name
    manifest.write_text(json.dumps(data))
    return manifest, repos


def test_reference_preflight_is_disposable_and_never_sent_to_model(tmp_path):
    manifest, repos = reference_suite(tmp_path, both=True)
    report = validate_suite(manifest, sandbox="host", trusted_host=True)
    assert report["status"] == "validated" and report["cases"] == 2
    for value in report["baselines"].values():
        assert value["failed"] == 1
        assert value["reference"]["changed_files"] == ["app.py"]
        assert value["reference"]["visible"]["passed"] == 1
        assert value["reference"]["holdout"]["passed"] == 1

    class Observe(ScriptedModel):
        def complete(self, messages, tools, timeout):
            assert "PRIVATE_REFERENCE_SENTINEL" not in json.dumps(messages)
            return super().complete(messages, tools, timeout)

    evaluated = evaluate_suite(manifest, Observe, sandbox="host", trusted_host=True)
    assert evaluated["successes"] == 2
    assert "PRIVATE_REFERENCE_SENTINEL" not in json.dumps(evaluated)
    assert "return a - b" in (repos[0] / "app.py").read_text()
    assert not list(repos[0].rglob("test_selfforge_holdout_*"))


def test_reference_is_required_before_any_validation_execution(tmp_path, monkeypatch):
    manifest, _ = _manifest(tmp_path)
    monkeypatch.setattr(benchmark, "_baseline", lambda *_args, **_kwargs: pytest.fail("No test run"))
    with pytest.raises(ValueError, match="reference_patch for every case"):
        validate_suite(manifest, sandbox="host", trusted_host=True)


def test_reference_preflight_and_private_failure_snapshots_compose(tmp_path):
    manifest, _ = reference_suite(tmp_path)

    class Stops:
        def complete(self, messages, _tools, _timeout):
            assert "PRIVATE_REFERENCE_SENTINEL" not in json.dumps(messages)
            return ModelTurn({"role": "assistant", "content": "Unable to finish."},
                             "stop", "fixture", "composition", None)

    directory = tmp_path / "private"
    report = evaluate_suite(manifest, lambda _case: Stops(), sandbox="host", trusted_host=True,
                            failure_artifacts=directory)
    assert report["baselines"]["addition"]["reference"]["status"] == "validated"
    outcome = report["results"][0]
    assert outcome["reason"] == "no_code_change" and report["successes"] == 0
    path = directory / report["failure_artifact_run"] / outcome["failure_artifact_id"]
    snapshot = json.loads(path.read_text())
    assert snapshot["visible"]["failure_details"] and snapshot["holdout"]["failure_details"]
    assert "PRIVATE_REFERENCE_SENTINEL" not in path.read_text()
    assert "AssertionError" not in json.dumps(report)
    assert str(directory) not in json.dumps(report)


def test_invalid_reference_prevents_model_and_artifact_creation(tmp_path):
    manifest, _ = reference_suite(tmp_path)
    (tmp_path / "addition.patch").write_text("invalid reference")
    directory = tmp_path / "private"
    with pytest.raises(ValueError, match="invalid paths or cannot apply"):
        evaluate_suite(manifest, lambda _case: pytest.fail("No model construction"),
                       sandbox="host", trusted_host=True, failure_artifacts=directory)
    assert not directory.exists()


@pytest.mark.parametrize("problem", ["malformed", "wrong_context", "unallowed_path"])
def test_invalid_reference_stops_before_model(tmp_path, problem):
    manifest, repos = reference_suite(tmp_path)
    reference = tmp_path / "addition.patch"
    old = (repos[0] / "app.py").read_text()
    if problem == "malformed":
        reference.write_text("not a patch")
    elif problem == "wrong_context":
        reference.write_text(patch(old.replace("a - b", "a * b"), old.replace("a - b", "a + b")))
    else:
        original = (repos[0] / "tests/test_app.py").read_text()
        reference.write_text(patch(original, original + "# changed\n", "tests/test_app.py"))
    with pytest.raises(ValueError, match="invalid paths or cannot apply"):
        evaluate_suite(manifest, lambda _case: pytest.fail("No model construction"),
                        sandbox="host", trusted_host=True)


@pytest.mark.parametrize("phase", ["visible", "holdout"])
def test_reference_must_pass_both_independent_selections(tmp_path, phase):
    manifest, repos = reference_suite(tmp_path)
    old = (repos[0] / "app.py").read_text()
    if phase == "holdout":
        new = old.replace("return a - b", "return a + b if a >= 0 else 0")
        (tmp_path / "addition.patch").write_text(patch(old, new))
    else:
        payload = json.loads(manifest.read_text())
        payload["cases"][0]["allowed_changes"].append("tests/test_app.py")
        manifest.write_text(json.dumps(payload))
        original = (repos[0] / "tests/test_app.py").read_text()
        with (tmp_path / "addition.patch").open("a") as handle:
            handle.write(patch(original, original.replace("== 5", "== 99"), "tests/test_app.py"))
    with pytest.raises(ValueError, match=f"reference {phase} tests did not pass"):
        validate_suite(manifest, sandbox="host", trusted_host=True)


def test_reference_snapshot_survives_manifest_and_patch_changes(tmp_path, monkeypatch):
    manifest, _ = reference_suite(tmp_path)
    original_manifest = manifest.read_bytes()
    original_patch = (tmp_path / "addition.patch").read_bytes()
    baseline = benchmark._baseline
    def mutate(case, **kwargs):
        manifest.write_text('{"version":1,"cases":[]}')
        (tmp_path / "addition.patch").write_text("changed after freeze")
        return baseline(case, **kwargs)
    monkeypatch.setattr(benchmark, "_baseline", mutate)
    result = validate_suite(manifest, sandbox="host", trusted_host=True)
    assert result["manifest_sha256"] == hashlib.sha256(original_manifest).hexdigest()
    reference = result["baselines"]["addition"]["reference"]
    assert reference["patch_sha256"] == hashlib.sha256(original_patch).hexdigest()


def test_reference_outputs_with_only_skips_are_rejected(tmp_path, monkeypatch):
    manifest, _ = reference_suite(tmp_path)
    monkeypatch.setattr(benchmark, "_baseline", lambda *_args, **_kwargs: {"failed": 1})
    class Runner:
        def run(self, *_args, **_kwargs):
            return SimpleNamespace(status="passed", passed=0, failed=0, errors=0,
                                   skipped=1, exit_code=0)
    monkeypatch.setattr(benchmark, "_runner", lambda *_args: Runner())
    with pytest.raises(ValueError, match="reference visible tests did not pass"):
        validate_suite(manifest, sandbox="host", trusted_host=True)


def test_visible_reference_test_cannot_modify_holdout_workspace(tmp_path):
    manifest, repos = reference_suite(tmp_path)
    test_path = repos[0] / "tests/test_app.py"
    test_path.write_text(
        "from app import add\nfrom pathlib import Path\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
        "    Path('app.py').write_text('def add(a, b): return 0\\n')\n")
    _git(repos[0], "add", "tests/test_app.py")
    _git(repos[0], "commit", "-qm", "Visible test changes only its disposable checkout")
    payload = json.loads(manifest.read_text())
    payload["cases"][0]["revision"] = _git(repos[0], "rev-parse", "HEAD")
    manifest.write_text(json.dumps(payload))
    result = validate_suite(manifest, sandbox="host", trusted_host=True)
    assert result["baselines"]["addition"]["reference"]["holdout"]["passed"] == 1


@pytest.mark.parametrize("problem", ["internal", "oversized", "empty"])
def test_reference_input_boundaries(tmp_path, problem):
    manifest, repos = reference_suite(tmp_path)
    if problem == "internal":
        (repos[0] / "reference.patch").write_text((tmp_path / "addition.patch").read_text())
        payload = json.loads(manifest.read_text())
        payload["cases"][0]["reference_patch"] = "addition/reference.patch"
        manifest.write_text(json.dumps(payload))
    else:
        (tmp_path / "addition.patch").write_bytes(b"x" * (262145 if problem == "oversized" else 0))
    with pytest.raises(ValueError):
        load_suite(manifest)


def test_validation_cli_does_not_resolve_provider_keys(tmp_path, monkeypatch, capsys):
    manifest, _ = reference_suite(tmp_path)
    output = tmp_path / "validation.json"
    monkeypatch.setattr(cli, "route_providers", lambda *_args: pytest.fail("No provider needed"))
    monkeypatch.setattr(sys, "argv", ["selfforge-agent", "eval-validate", "--manifest", str(manifest),
                                      "--sandbox", "host", "--trust-local", "--output", str(output)])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"
    assert json.loads(output.read_text())["cases"] == 1
    with pytest.raises(ValueError, match="explicit trust"):
        validate_suite(manifest, sandbox="host")


@pytest.mark.integration
def test_real_docker_reference_validation(tmp_path):
    for probe in (["docker", "info"], ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    manifest, _ = reference_suite(tmp_path)
    report = validate_suite(manifest)
    assert report["baselines"]["addition"]["reference"]["visible"]["passed"] == 1
    assert report["baselines"]["addition"]["reference"]["holdout"]["passed"] == 1
    assert report["images"]["addition"]["id"].startswith("sha256:")
