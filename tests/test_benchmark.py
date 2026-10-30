"""Objective repeated evaluation over pinned disposable Git checkouts."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime import benchmark
from selfforge_agent.runtime.benchmark import (
    BenchmarkCase,
    BenchmarkOutcome,
    evaluate_suite,
    load_suite,
)
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def _manifest(root: Path) -> tuple[Path, list[Path]]:
    cases = []
    repositories = []
    hidden = root / "hidden"
    hidden.mkdir()
    fixtures = [
        ("addition", "def add(a, b):\n    return a - b\n",
         "from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
         "from app import add\n\ndef test_holdout():\n    assert add(-4, 9) == 5\n"),
        ("normalization", "def normalize(name):\n    return name.upper()\n",
         ("from app import normalize\n\ndef test_normalize():\n"
          "    assert normalize('  Alice  ') == 'alice'\n"),
         ("from app import normalize\n\ndef test_holdout():\n"
          "    assert normalize('  BOB ') == 'bob'\n")),
    ]
    for case_id, source, visible, holdout in fixtures:
        repo = root / case_id
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        (repo / "app.py").write_text(source, encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_app.py").write_text(visible, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "Pinned broken baseline")
        (hidden / f"test_{case_id}.py").write_text(holdout, encoding="utf-8")
        cases.append({"id": case_id, "repository": case_id,
                      "revision": _git(repo, "rev-parse", "HEAD"),
                      "task": f"Fix {case_id} and run tests", "allowed_changes": ["app.py"],
                      "holdout_test": f"hidden/test_{case_id}.py"})
        repositories.append(repo)
    manifest = root / "suite.json"
    manifest.write_text(json.dumps({"version": 1, "cases": cases}), encoding="utf-8")
    return manifest, repositories


class ScriptedModel:
    def __init__(self, case: BenchmarkCase, *, cheat: bool = False) -> None:
        self.index = 0
        self.case = case
        old, new = (("return a - b", "return a + b") if case.id == "addition"
                    else ("return name.upper()", "return name.strip().lower()"))
        if cheat:
            self.path = "tests/test_app.py"
            self.old = "assert add(2, 3) == 5"
            self.new = "assert True"
        else:
            self.path, self.old, self.new = "app.py", old, new

    def complete(self, _messages: list[dict], _tools: list[dict], _timeout: float) -> ModelTurn:
        actions = [
            ("read_file", {"path": self.path}),
            ("edit_file", {"path": self.path, "old_text": self.old, "new_text": self.new}),
            ("run_tests", {"path": self.case.agent_test_path,
                           **({"keyword": self.case.agent_test_keyword}
                              if self.case.agent_test_keyword else {})}),
        ]
        self.index += 1
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        if self.index <= len(actions):
            name, arguments = actions[self.index - 1]
            return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
                "id": str(self.index), "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}, "tool_calls", "fixture", "scripted", usage)
        return ModelTurn({"role": "assistant", "content": "Done"},
                         "stop", "fixture", "scripted", usage)


def test_repeated_suite_uses_fresh_repos_and_hidden_tests(tmp_path: Path) -> None:
    manifest, repositories = _manifest(tmp_path)
    report = evaluate_suite(manifest, ScriptedModel, repeats=2,
                            sandbox="host", trusted_host=True)
    assert (report["cases"], report["runs"], report["successes"]) == (2, 4, 4), report["results"]
    assert report["success_rate"] == 1.0
    assert report["total_tokens"] == 4 * 4 * 15
    assert report["failure_reasons"] == {}
    assert all(item["holdout_passed"] == 1 and item["changed_files"] == ["app.py"]
               for item in report["results"])
    assert all(item["visible_status"] == "passed" and item["visible_passed"] == 1
               for item in report["results"])
    assert all(item["agent_steps"] == 4 and item["tools_used"] == 3
               and item["tool_trace"] == [
                   {"name": "read_file", "ok": True},
                   {"name": "edit_file", "ok": True},
                   {"name": "run_tests", "ok": True},
               ] for item in report["results"])
    assert "return a - b" not in json.dumps(report)
    assert "return a - b" in (repositories[0] / "app.py").read_text()
    assert not any(repo.joinpath("tests", "test_selfforge_holdout_addition.py").exists()
                   for repo in repositories)


def test_failed_runs_keep_opt_in_private_diagnostics_outside_public_report(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)

    class Claims:
        def complete(self, *_args):
            return ModelTurn({"role": "assistant", "content": "Done"}, "stop", "fixture", "claim", None)

    artifacts = tmp_path / "private"
    report = evaluate_suite(manifest, lambda _case: Claims(), sandbox="host", trusted_host=True,
                            failure_artifacts=artifacts)
    run = artifacts / report["failure_artifact_run"]
    assert run.stat().st_mode & 0o777 == 0o700
    for outcome in report["results"]:
        path = run / outcome["failure_artifact_id"]
        assert path.stat().st_mode & 0o777 == 0o600
        payload = json.loads(path.read_text())
        assert payload["visible"]["failure_details"]
        assert payload["visible"]["failed"] == 1
        assert "stdout" not in payload["visible"]
        assert "command" not in payload["visible"]
        assert payload["reason"] == outcome["reason"] == "no_code_change"
    published = json.dumps(report)
    assert "AssertionError" not in published and "test_app.test_add" not in published
    assert str(artifacts) not in published
    assert len(list(run.glob("*.json"))) == 2


def test_successful_suite_does_not_write_failure_snapshots(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    artifacts = tmp_path / "private"
    report = evaluate_suite(manifest, ScriptedModel, sandbox="host", trusted_host=True,
                            failure_artifacts=artifacts)
    assert report["successes"] == 2
    assert not list(artifacts.rglob("*.json"))
    assert all(not row["failure_artifact_id"] for row in report["results"])


def test_artifact_write_error_preserves_primary_failure(tmp_path: Path, monkeypatch) -> None:
    manifest, _ = _manifest(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("private path and credential must not enter report")

    monkeypatch.setattr(benchmark, "write_failure", broken)
    report = evaluate_suite(manifest, lambda case: ScriptedModel(case, cheat=True),
                            sandbox="host", trusted_host=True,
                            failure_artifacts=tmp_path / "private")
    assert report["successes"] == 0
    assert all(row["failure_artifact_error"] == "artifact_write_failed"
               for row in report["results"])
    assert all(row["reason"] != "harness_error:OSError" for row in report["results"])
    assert "credential" not in json.dumps(report)


def test_artifacts_cannot_be_placed_in_evaluated_repository(tmp_path: Path) -> None:
    manifest, repos = _manifest(tmp_path)
    with pytest.raises(ValueError, match="outside evaluated repositories"):
        evaluate_suite(manifest, lambda _case: pytest.fail("must stop before model creation"),
                       sandbox="host", trusted_host=True, failure_artifacts=repos[0] / "private")


def test_claims_and_visible_test_edits_cannot_pass_holdout(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    class ClaimsSuccess:
        def complete(self, _messages, _tools, _timeout):
            return ModelTurn({"role": "assistant", "content": "Fixed and tested"},
                             "stop", "fixture", "claim", None)

    claimed = evaluate_suite(manifest, lambda _case: ClaimsSuccess(),
                             sandbox="host", trusted_host=True)
    assert claimed["successes"] == 0
    assert claimed["results"][0]["reason"] == "no_code_change"
    assert claimed["results"][0]["holdout_failed"] == 1

    cheated = evaluate_suite(manifest, lambda case: ScriptedModel(case, cheat=True),
                             sandbox="host", trusted_host=True)
    assert cheated["successes"] == 0
    assert cheated["results"][0]["agent_status"] == "completed"
    assert cheated["results"][0]["reason"] == "unexpected_file_changes"
    assert cheated["results"][0]["holdout_failed"] == 1

    class BrokenProvider:
        def complete(self, _messages, _tools, _timeout):
            raise RuntimeError("provider unavailable")

    unavailable = evaluate_suite(manifest, lambda _case: BrokenProvider(),
                                 sandbox="host", trusted_host=True)
    assert unavailable["failure_reasons"] == {"agent_failed": 1}
    assert unavailable["agent_error_codes"] == {"runtime_error": 1}


def test_unrelated_green_test_cannot_hide_broken_visible_suite(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    payload["cases"][0]["allowed_changes"] = [
        "app.py", "tests/test_app.py", "tests/test_smoke.py",
    ]
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    class RunsUnrelatedTest:
        def __init__(self) -> None:
            self.index = 0

        def complete(self, _messages, _tools, _timeout):
            actions = [
                ("edit_file", {"path": "app.py", "old_text": "return a - b",
                               "new_text": "return a + b"}),
                ("edit_file", {"path": "tests/test_app.py",
                               "old_text": "assert add(2, 3) == 5",
                               "new_text": "assert add(2, 3) == 99"}),
                ("edit_file", {"path": "tests/test_smoke.py", "old_text": "",
                               "new_text": "def test_smoke():\n    assert True\n"}),
                ("run_tests", {"path": "tests/test_smoke.py"}),
            ]
            self.index += 1
            if self.index <= len(actions):
                name, arguments = actions[self.index - 1]
                return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
                    "id": str(self.index), "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}, "tool_calls", "fixture", "scripted", None)
            return ModelTurn({"role": "assistant", "content": "Fixed and tested"},
                             "stop", "fixture", "scripted", None)

    report = evaluate_suite(manifest, lambda _case: RunsUnrelatedTest(),
                            sandbox="host", trusted_host=True)
    result = report["results"][0]
    assert result["agent_status"] == "completed"
    assert result["holdout_status"] == "passed" and result["holdout_passed"] == 1
    assert result["visible_status"] == "failed" and result["visible_failed"] == 1
    assert result["reason"] == "visible_test_assertion_failed"
    assert report["successes"] == 0


def test_explicit_keyword_selects_relevant_visible_tests(tmp_path: Path) -> None:
    manifest, repositories = _manifest(tmp_path)
    repository = repositories[0]
    (repository / "tests" / "test_unrelated.py").write_text(
        "def test_unrelated():\n    assert False\n", encoding="utf-8")
    _git(repository, "add", "tests/test_unrelated.py")
    _git(repository, "commit", "-qm", "Add unrelated failing test")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    payload["cases"][0]["revision"] = _git(repository, "rev-parse", "HEAD")
    payload["cases"][0]["agent_test_keyword"] = "test_add"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate_suite(manifest, ScriptedModel, sandbox="host", trusted_host=True)
    assert report["successes"] == 1, report["results"]
    result = report["results"][0]
    assert result["visible_status"] == "passed" and result["visible_passed"] == 1
    assert result["holdout_status"] == "passed"


def test_provider_failure_code_omits_raw_error_text() -> None:
    assert benchmark._agent_error_code(
        "All providers failed: deepseek: Provider returned HTTP 429; token=secret"
    ) == "provider_http_429"
    assert benchmark._agent_error_code("Model stopped with length") == "model_finish_reason"


def test_tool_diagnostics_sanitize_unknown_names(tmp_path: Path) -> None:
    session = SessionStore(tmp_path)
    session.trace("tool", name="secret embedded in tool name", ok=False)
    session.trace("tool", name={"secret": "nested"}, ok=False)
    session.trace("tool", name="run_tests", ok=True)
    assert benchmark._tool_trace(session) == [
        {"name": "unknown", "ok": False}, {"name": "unknown", "ok": False},
        {"name": "run_tests", "ok": True}]


def test_manifest_requires_pinned_commit_and_external_holdout(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"][0]["revision"] = "main"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_suite(manifest)

    payload["cases"][0]["revision"] = _git(tmp_path / "addition", "rev-parse", "HEAD")
    payload["cases"][0]["holdout_test"] = "addition/tests/test_app.py"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="outside the agent repository"):
        load_suite(manifest)
    with pytest.raises(ValueError, match="explicit trust"):
        evaluate_suite(manifest, ScriptedModel, sandbox="host")


def test_suite_rejects_holdout_that_already_passes_baseline(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "hidden" / "test_addition.py").write_text(
        "from app import add\n\ndef test_holdout():\n    assert add(2, 3) == -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="failing assertion on its pinned baseline"):
        evaluate_suite(manifest, ScriptedModel, sandbox="host", trusted_host=True)


def test_per_case_docker_images_are_pinned_before_all_runs(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"][1]["image"] = "custom-project:one"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    resolved = {"default-project:one": "sha256:default", "custom-project:one": "sha256:custom"}
    inspected = []
    used = []

    def image_id(reference: str) -> str:
        inspected.append(reference)
        return resolved[reference]

    def baseline(case, *, image, **_kwargs):
        used.append((case.spec.id, image, "baseline"))
        return {"status": "failed"}

    def run_once(case, repeat, _factory, *, image, **_kwargs):
        used.append((case.spec.id, image, repeat))
        return BenchmarkOutcome(case.spec.id, repeat, True, "passed", "completed", ["app.py"],
                                "passed", 1, 0, 0, 0, 0.1, "fixture", "scripted", 0, 0, 0)

    monkeypatch.setattr(benchmark, "_image_id", image_id)
    monkeypatch.setattr(benchmark, "_baseline", baseline)
    monkeypatch.setattr(benchmark, "_run_once", run_once)
    report = evaluate_suite(manifest, ScriptedModel, repeats=2, image="default-project:one")
    assert inspected == ["default-project:one", "custom-project:one"]
    assert used == [
        ("addition", "sha256:default", "baseline"),
        ("normalization", "sha256:custom", "baseline"),
        ("addition", "sha256:default", 1),
        ("addition", "sha256:default", 2),
        ("normalization", "sha256:custom", 1),
        ("normalization", "sha256:custom", 2),
    ]
    assert report["image_id"] is None
    assert report["images"]["normalization"] == {
        "reference": "custom-project:one", "id": "sha256:custom"}


def test_report_hashes_the_manifest_snapshot_used_for_cases(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _ = _manifest(tmp_path)
    original = manifest.read_bytes()

    def baseline(_case, **_kwargs):
        manifest.write_text('{"version": 1, "cases": []}', encoding="utf-8")
        return {"status": "failed"}

    def run_once(case, repeat, _factory, **_kwargs):
        return BenchmarkOutcome(case.spec.id, repeat, True, "passed", "completed", ["app.py"],
                                "passed", 1, 0, 0, 0, 0.1, "fixture", "scripted", 0, 0, 0)

    monkeypatch.setattr(benchmark, "_baseline", baseline)
    monkeypatch.setattr(benchmark, "_run_once", run_once)
    report = evaluate_suite(manifest, ScriptedModel, sandbox="host", trusted_host=True)
    assert report["cases"] == 2
    assert report["manifest_sha256"] == hashlib.sha256(original).hexdigest()


def test_manifest_rejects_invalid_case_image(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"][0]["image"] = "example image"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Docker image"):
        load_suite(manifest)


@pytest.mark.integration
def test_suite_runs_agent_and_holdout_in_live_docker(tmp_path: Path) -> None:
    image = "selfforge-agent-sandbox:latest"
    for probe in (["docker", "info"], ["docker", "image", "inspect", image]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    report = evaluate_suite(manifest, ScriptedModel)
    assert report["successes"] == 1, report["results"]
    assert report["results"][0]["holdout_status"] == "passed"
    assert report["results"][0]["visible_status"] == "passed"
    assert report["image_id"].startswith("sha256:")
