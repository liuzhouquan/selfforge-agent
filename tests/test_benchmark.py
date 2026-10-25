"""Objective repeated evaluation over pinned disposable Git checkouts."""

import json
import subprocess
from pathlib import Path

import pytest

from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime.benchmark import BenchmarkCase, evaluate_suite, load_suite
from selfforge_agent.runtime.model import ModelTurn


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
            ("run_tests", {"path": "tests"}),
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
    assert "return a - b" in (repositories[0] / "app.py").read_text()
    assert not any(repo.joinpath("tests", "test_selfforge_holdout_addition.py").exists()
                   for repo in repositories)


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
    assert report["image_id"].startswith("sha256:")
