"""Independent verification must observe the Agent's final source snapshot."""

import json
from types import SimpleNamespace

import pytest
from test_benchmark import _git, _manifest

from selfforge_agent.runtime import benchmark


@pytest.mark.parametrize("correct_agent_source", [False, True])
def test_visible_test_writes_cannot_change_holdout_result(
    tmp_path, monkeypatch, correct_agent_source,
):
    manifest, repos = _manifest(tmp_path)
    repo = repos[0]
    correct = "def add(a, b):\n    return a + b\n"
    incorrect = "def add(a, b):\n    return a - b + 1\n"
    rewritten = incorrect if correct_agent_source else correct
    (repo / "tests/test_app.py").write_text(
        "from pathlib import Path\n\ndef test_side_effect():\n"
        f"    Path('app.py').write_text({rewritten!r})\n"
        "    assert True\n"
    )
    _git(repo, "add", "tests/test_app.py")
    _git(repo, "commit", "-qm", "Visible suite mutates its own verification copy")
    payload = json.loads(manifest.read_text())
    payload["cases"][0]["revision"] = _git(repo, "rev-parse", "HEAD")
    manifest.write_text(json.dumps(payload))
    _, cases = benchmark.load_suite(manifest)

    class FinishedAgent:
        # Target verification after the loop, not the loop's completion gate.
        def __init__(self, _model, tools, _session, **_kwargs):
            self.root = tools.root

        def run(self, _task):
            (self.root / "app.py").write_text(correct if correct_agent_source else incorrect)
            return SimpleNamespace(status="completed", steps=0, tools_used=0, error="")

    monkeypatch.setattr(benchmark, "CodingAgent", FinishedAgent)
    outcome = benchmark._run_once(
        cases[0], 1, lambda _case: None, sandbox="host", trusted_host=True,
        image="unused", max_steps=1, time_budget=10, test_timeout=10,
    )
    assert outcome.visible_status == "passed" and outcome.visible_passed == 1
    assert outcome.changed_files == ["app.py"]
    assert outcome.success is correct_agent_source
    assert outcome.reason == ("passed" if correct_agent_source else "holdout_assertion_failed")
    assert outcome.holdout_passed == int(correct_agent_source)
    assert outcome.holdout_failed == int(not correct_agent_source)
    assert "return a - b\n" in (repo / "app.py").read_text()
