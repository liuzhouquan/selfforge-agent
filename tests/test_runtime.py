import json
import subprocess
import tarfile
import threading
from pathlib import Path

from selfforge_agent.integrations.model_http import ProviderHTTPError
from selfforge_agent.providers import ProviderConfig
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.context import ContextManager
from selfforge_agent.runtime.evaluation import OfflineFixtureModel, evaluate
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ModelTurn, OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


def _call(name: str, arguments: dict, number: int) -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call-{number}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}, "tool_calls", "fixture", "scripted", None)


class ScriptedModel:
    def __init__(self) -> None:
        self.turns = [
            _call("search_code", {"pattern": "def add", "glob": "*.py"}, 1),
            _call("read_file", {"path": "calculator.py"}, 2),
            _call("edit_file", {"path": "calculator.py", "old_text": "return a - b",
                                "new_text": "return a + b"}, 3),
            _call("run_tests", {"path": "tests"}, 4),
            ModelTurn({"role": "assistant", "content": "Fixed add; tests passed."},
                      "stop", "fixture", "scripted", None),
        ]
        self.seen: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        assert timeout > 0
        assert {tool["function"]["name"] for tool in tools} >= {
            "read_file", "edit_file", "search_code", "bash", "run_tests"
        }
        self.seen.append(list(messages))
        return self.turns.pop(0)


def _fixture(root: Path) -> None:
    root.joinpath("calculator.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    root.joinpath("tests").mkdir()
    root.joinpath("tests", "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )


def test_native_tool_loop_edits_runs_tests_and_persists_session(tmp_path: Path) -> None:
    _fixture(tmp_path)
    model = ScriptedModel()
    session = SessionStore(tmp_path)
    agent = CodingAgent(model, ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                        session, max_steps=6)
    result = agent.run("Fix add and run tests")
    assert result.status == "completed"
    assert result.tools_used == 4
    assert "return a + b" in (tmp_path / "calculator.py").read_text()
    messages, status = session.load()
    assert status == "completed"
    assert messages[-2]["role"] == "tool"
    assert "exit=0" in messages[-2]["content"]
    assert model.seen[1][-1]["tool_call_id"] == "call-1"
    assert {event["event"] for event in session.trace_events()} >= {"model_call", "tool", "status"}


def test_session_can_resume_after_step_limit(tmp_path: Path) -> None:
    _fixture(tmp_path)
    session = SessionStore(tmp_path)
    model = ScriptedModel()
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    first = CodingAgent(model, tools, session, max_steps=1).run("Fix add")
    assert first.status == "max_steps"
    second = CodingAgent(model, tools, SessionStore(tmp_path, first.session_id),
                         max_steps=5).run(resume=True)
    assert second.status == "completed"
    assert second.session_id == first.session_id


def test_workspace_rejects_traversal_and_untrusted_host(tmp_path: Path) -> None:
    try:
        HostExecutor(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("Host execution accepted an untrusted repository")
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    assert not tools.execute("read_file", {"path": "../outside"}).ok
    assert not tools.execute("edit_file", {"path": "../outside", "old_text": "",
                                                "new_text": "bad"}).ok
    (tmp_path / ".env").write_text("SECRET=test", encoding="utf-8")
    assert not tools.execute("read_file", {"path": ".env"}).ok
    assert not (tmp_path.parent / "outside").exists()


def test_context_compaction_keeps_task_and_summarizes_tools() -> None:
    manager = ContextManager(500)
    messages = [
        {"role": "system", "content": "Keep tests unchanged"},
        {"role": "user", "content": "Fix calculator"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 4_000},
    ]
    compacted, changed = manager.compact(messages)
    assert changed
    assert compacted[0]["content"] == "Keep tests unchanged"
    assert compacted[1]["content"] == "Fix calculator"
    assert "read_file" in compacted[2]["content"]
    assert manager.estimate(compacted) < 500


def test_docker_executor_restricts_network_and_resources(tmp_path: Path, monkeypatch) -> None:
    captured = []
    (tmp_path / ".env").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".env.production").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / "app.py").write_text("print(1)", encoding="utf-8")

    def fake_run(argv, **kwargs):
        captured.append(argv)
        if kwargs.get("stdin") is not None:
            with tarfile.open(fileobj=kwargs["stdin"]) as archive:
                names = archive.getnames()
            assert "app.py" in names
            assert ".env" not in names
            assert ".env.production" not in names
            assert not any(name.startswith(".aws") for name in names)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr("selfforge_agent.runtime.execution.subprocess.run", fake_run)
    result = DockerExecutor(tmp_path).run("python -m pytest tests", 20)
    argv = captured[0]
    assert result.returncode == 0
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "--cap-drop" in argv and "--memory" in argv
    assert any(item.startswith("/workspace:rw,nosuid") for item in argv)
    assert not any("type=bind" in item for item in argv)


def test_objective_evaluation_reports_real_test_results() -> None:
    report = evaluate(OfflineFixtureModel)
    assert report["tasks"] == 2
    assert report["successes"] == 2
    assert all(item["changed"] and item["test_exit_code"] == 0
               for item in report["results"])


def test_evaluation_does_not_trust_model_claims() -> None:
    class ClaimsSuccess:
        def complete(self, _messages, _tools, _timeout):
            return ModelTurn({"role": "assistant", "content": "I fixed it and tests passed."},
                             "stop", "fixture", "claim", None)

    report = evaluate(lambda _case: ClaimsSuccess())
    assert report["successes"] == 0
    assert all(item["test_exit_code"] != 0 for item in report["results"])


def test_cancelled_run_records_state(tmp_path: Path) -> None:
    event = threading.Event()
    event.set()
    session = SessionStore(tmp_path)
    result = CodingAgent(ScriptedModel(),
                         ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         session, cancel=event).run("Do a task")
    assert result.status == "cancelled"
    assert session.load()[1] == "cancelled"


def test_native_model_adapter_falls_back_and_keeps_tool_call(monkeypatch) -> None:
    providers = [
        ProviderConfig(name="deepseek", label="DeepSeek", key="test-a", model="a",
                       base_url="https://example.com/a", status_url=""),
        ProviderConfig(name="openrouter", label="OpenRouter", key="test-b", model="b",
                       base_url="https://example.com/b", status_url=""),
    ]
    seen = []

    def fake_request(url, key, **kwargs):
        seen.append((url, key, kwargs["payload"]["tools"]))
        if key == "test-a":
            raise ProviderHTTPError("unavailable", status_code=503)
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {
                "name": "read_file", "arguments": '{"path":"app.py"}'}}]}}]}

    monkeypatch.setattr("selfforge_agent.runtime.model.request_json", fake_request)
    turn = OpenAIChatAdapter(providers).complete([{"role": "user", "content": "task"}],
                                                  [{"type": "function"}], 5)
    assert turn.provider == "openrouter"
    assert turn.message["tool_calls"][0]["function"]["name"] == "read_file"
    assert len(seen) == 2


def test_docker_timeout_forces_container_cleanup(tmp_path: Path, monkeypatch) -> None:
    from selfforge_agent.runtime import execution

    monkeypatch.setattr(execution, "_run", lambda *_args: execution.CommandResult(None, "", True))
    removed = []
    monkeypatch.setattr(execution.subprocess, "run",
                        lambda argv, **_kwargs: removed.append(argv))
    assert DockerExecutor(tmp_path).run("sleep 10", 1).timed_out
    assert removed and removed[0][:3] == ["docker", "rm", "-f"]
