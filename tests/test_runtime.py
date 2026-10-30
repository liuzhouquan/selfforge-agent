import json
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

from selfforge_agent.integrations.model_http import ProviderHTTPError
from selfforge_agent.providers import ProviderConfig
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.context import ContextManager
from selfforge_agent.runtime.evaluation import OfflineFixtureModel, evaluate
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ModelTurn, OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import EditPolicy, ToolRegistry


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


def test_session_recovers_torn_event_before_next_append(tmp_path: Path) -> None:
    session = SessionStore(tmp_path)
    session.record_message({"role": "user", "content": "Keep this task"})
    session.record_status("max_steps")
    session.record_verification(True, workspace_sha256="1" * 64)
    with session.path.open("ab") as handle:
        handle.write(b'{"type":"message","content":"\xe4')

    assert session.load() == ([{"role": "user", "content": "Keep this task"}], "max_steps")
    assert session.verification_passed()

    session.record_status("running")
    assert session.load()[1] == "running"
    assert session.verification_passed()
    assert len(session.path.read_bytes().splitlines()) == 4


def test_agent_resumes_after_torn_session_event(tmp_path: Path) -> None:
    _fixture(tmp_path)
    session = SessionStore(tmp_path)
    model = ScriptedModel()
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    first = CodingAgent(model, tools, session, max_steps=1).run("Fix add")
    assert first.status == "max_steps"
    with session.path.open("ab") as handle:
        handle.write(b'{"type":"message"')

    resumed = CodingAgent(model, tools, SessionStore(tmp_path, first.session_id),
                          max_steps=5).run(resume=True)
    assert resumed.status == "completed"
    assert session.load()[1] == "completed"


def test_resume_preserves_successful_test_evidence(tmp_path: Path) -> None:
    _fixture(tmp_path)
    class Model:
        def __init__(self):
            self.turns = [_call("edit_file", {"path": "calculator.py",
                                             "old_text": "return a - b",
                                             "new_text": "return a + b"}, 1),
                          _call("run_tests", {"path": "tests"}, 2),
                          ModelTurn({"role": "assistant", "content": "Fixed and verified."},
                                    "stop", "fixture", "scripted", None)]
            self.tools_seen = []

        def complete(self, _messages, tools, _timeout):
            self.tools_seen.append(tools)
            return self.turns.pop(0)

    model = Model()
    session = SessionStore(tmp_path)
    registry = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    first = CodingAgent(model, registry, session, max_steps=2).run("Fix add")
    assert first.status == "max_steps"
    resumed = CodingAgent(model, registry, SessionStore(tmp_path, first.session_id),
                          max_steps=1).run(resume=True)
    assert resumed.status == "completed"
    assert model.tools_seen[-1] == []


def test_new_task_on_resume_needs_fresh_test_evidence(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")

    class Model:
        def __init__(self):
            self.turns = [_call("run_tests", {"path": "tests"}, 1),
                          ModelTurn({"role": "assistant", "content": "New task answered."},
                                    "stop", "fixture", "scripted", None)]

        def complete(self, _messages, _tools, _timeout):
            return self.turns.pop(0)

    model = Model()
    session = SessionStore(tmp_path)
    registry = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    first = CodingAgent(model, registry, session, max_steps=1).run("Check add")
    assert first.status == "max_steps" and session.verification_passed()
    second = CodingAgent(model, registry, SessionStore(tmp_path, first.session_id),
                         max_steps=1).run("Also do another task", resume=True)
    assert second.status == "unverified"
    assert not session.verification_passed()


def test_last_step_after_success_requests_final_answer_without_tools(tmp_path: Path) -> None:
    _fixture(tmp_path)
    class Model:
        def __init__(self):
            self.turns = [_call("run_tests", {"path": "tests"}, 1),
                          ModelTurn({"role": "assistant", "content": "Verified."},
                                    "stop", "fixture", "scripted", None)]
            self.tools_seen = []

        def complete(self, _messages, tools, _timeout):
            self.tools_seen.append(tools)
            return self.turns.pop(0)

    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    model = Model()
    result = CodingAgent(model, ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         SessionStore(tmp_path), max_steps=2).run("Check add")
    assert result.status == "completed"
    assert len(model.tools_seen[0]) > 0
    assert model.tools_seen[1] == []


def test_last_step_rejects_unrequested_tool_call(tmp_path: Path) -> None:
    _fixture(tmp_path)
    class Model:
        def __init__(self):
            self.turns = [_call("run_tests", {"path": "tests"}, 1),
                          _call("edit_file", {"path": "calculator.py",
                                              "old_text": "return a + b",
                                              "new_text": "return a - b"}, 2)]
            self.tools_seen = []

        def complete(self, _messages, tools, _timeout):
            self.tools_seen.append(tools)
            return self.turns.pop(0)

    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    model = Model()
    session = SessionStore(tmp_path)
    result = CodingAgent(model, ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         session, max_steps=2).run("Check add")
    assert result.status == "max_steps"
    assert result.tools_used == 1
    assert model.tools_seen[1] == []
    assert "return a + b" in (tmp_path / "calculator.py").read_text()
    assert session.load()[0][-1]["content"].startswith("Tool budget exhausted")


def test_empty_final_answer_cannot_complete_verified_run(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")

    class Model:
        def __init__(self):
            self.turns = [_call("run_tests", {"path": "tests"}, 1),
                          ModelTurn({"role": "assistant", "content": " "},
                                    "stop", "fixture", "scripted", None)]

        def complete(self, _messages, _tools, _timeout):
            return self.turns.pop(0)

    result = CodingAgent(Model(), ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         SessionStore(tmp_path), max_steps=2).run("Check add")
    assert result.status == "failed"
    assert "empty final answer" in result.error


def test_failed_test_revokes_prior_success(tmp_path: Path) -> None:
    _fixture(tmp_path)
    class Model:
        def __init__(self):
            self.turns = [_call("run_tests", {"path": "tests"}, 1),
                          _call("run_tests", {"path": "missing"}, 2),
                          ModelTurn({"role": "assistant", "content": "Done."},
                                    "stop", "fixture", "scripted", None)]

        def complete(self, _messages, _tools, _timeout):
            return self.turns.pop(0)

    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    session = SessionStore(tmp_path)
    result = CodingAgent(Model(), ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         session, max_steps=3).run("Check add")
    assert result.status == "unverified"
    assert not session.verification_passed()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_host_command_after_test_invalidates_verification(tmp_path: Path,
                                                          exit_code: int) -> None:
    _fixture(tmp_path)

    class Model:
        def __init__(self):
            self.turns = [
                _call("edit_file", {"path": "calculator.py", "old_text": "return a - b",
                                    "new_text": "return a + b"}, 1),
                _call("run_tests", {"path": "tests"}, 2),
                _call("bash", {"command": "sed -i 's/return a + b/return a - b/' "
                                            f"calculator.py; exit {exit_code}"}, 3),
                ModelTurn({"role": "assistant", "content": "Done."},
                          "stop", "fixture", "scripted", None),
            ]

        def complete(self, _messages, _tools, _timeout):
            return self.turns.pop(0)

    session = SessionStore(tmp_path)
    result = CodingAgent(Model(), ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                         session, max_steps=4).run("Fix add")
    assert "return a - b" in (tmp_path / "calculator.py").read_text()
    assert result.status == "unverified"
    assert not session.verification_passed()


def test_trace_tail_ignores_incomplete_concurrent_event(tmp_path: Path) -> None:
    session = SessionStore(tmp_path)
    session.trace("first")
    session.trace("second")
    with session.trace_path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "unfinished"')
    assert [event["event"] for event in session.trace_events(limit=1)] == ["second"]


def test_trace_tail_with_incomplete_utf8_recovers_on_next_append(tmp_path: Path) -> None:
    session = SessionStore(tmp_path)
    session.trace("first")
    with session.trace_path.open("ab") as handle:
        handle.write(b'{"event":"\xe4')

    assert [event["event"] for event in session.trace_events()] == ["first"]
    session.trace("second")
    assert [event["event"] for event in session.trace_events()] == ["first", "second"]


def test_complete_malformed_session_event_is_not_silently_discarded(tmp_path: Path) -> None:
    session = SessionStore(tmp_path)
    session.record_message({"role": "user", "content": "Keep this task"})
    with session.path.open("ab") as handle:
        handle.write(b'{"type":\n')

    with pytest.raises(json.JSONDecodeError):
        session.load()
    with pytest.raises(json.JSONDecodeError):
        session.verification_passed()


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
    (tmp_path / ".env.production").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(".env")
    assert not tools.execute("read_file", {"path": ".env"}).ok
    assert not tools.execute("read_file", {"path": ".env.production"}).ok
    assert not tools.execute("read_file", {"path": ".aws/credentials"}).ok
    assert not tools.execute("read_file", {"path": "alias.py"}).ok
    assert "SECRET" not in tools.execute("search_code", {"pattern": "SECRET", "glob": "*"}).content
    assert not (tmp_path.parent / "outside").exists()


def test_large_source_can_be_searched_and_read_by_bounded_line_range(tmp_path: Path) -> None:
    source = tmp_path / "large.py"
    source.write_text("x = 0\n" * 22_000 + "def target():\n    return 42\n", encoding="utf-8")
    tools = ToolRegistry(tmp_path)

    whole = tools.execute("read_file", {"path": "large.py"})
    assert not whole.ok and "start_line and end_line" in whole.content
    search = tools.execute("search_code", {"pattern": "def target", "glob": "*.py"})
    assert search.ok and "large.py:22001: def target():" in search.content

    selected = tools.execute("read_file", {"path": "large.py",
                                           "start_line": 22_001, "end_line": 22_002})
    assert selected.ok and selected.content == "def target():\n    return 42\n"
    assert not tools.execute("read_file", {"path": "large.py", "start_line": 1}).ok
    assert not tools.execute("read_file", {"path": "large.py",
                                                "start_line": 1, "end_line": 201}).ok
    assert not tools.execute("read_file", {"path": "large.py",
                                                "start_line": 22_100, "end_line": 22_101}).ok


def test_run_tests_keyword_selects_relevant_tests(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_selection.py").write_text(
        "def test_relevant():\n    assert True\n\n"
        "def test_unrelated():\n    assert False\n", encoding="utf-8")
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    assert not tools.execute("run_tests", {"path": "tests"}).ok
    selected = tools.execute("run_tests", {"path": "tests", "keyword": "relevant"})
    assert selected.ok and "1 passed" in selected.content
    assert not tools.execute("run_tests", {"path": "tests", "keyword": " "}).ok


def test_restricted_edit_policy_blocks_unapproved_and_unsafe_changes(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text("return a - b\n", encoding="utf-8")
    (tmp_path / "tests.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to("calculator.py")
    tools = ToolRegistry(tmp_path, edit_policy=EditPolicy(
        allowed_paths=frozenset({"calculator.py", "alias.py", "new.py"}),
        max_file_bytes=64, allow_create=False))
    names = {item["function"]["name"] for item in tools.schemas()}
    assert names == {"read_file", "edit_file", "search_code"}
    assert not tools.execute("bash", {"command": "true"}).ok
    assert not tools.execute("edit_file", {"path": "tests.py", "old_text": "assert True",
                                               "new_text": "assert False"}).ok
    assert not tools.execute("edit_file", {"path": "new.py", "old_text": "",
                                               "new_text": "new"}).ok
    assert not tools.execute("edit_file", {"path": "alias.py", "old_text": "a - b",
                                               "new_text": "a + b"}).ok
    assert not tools.execute("edit_file", {"path": "calculator.py", "old_text": "a - b",
                                               "new_text": "a - b"}).ok
    assert not tools.execute("edit_file", {"path": "calculator.py", "old_text": "a - b",
                                               "new_text": "x" * 65}).ok
    assert tools.execute("edit_file", {"path": "calculator.py", "old_text": "a - b",
                                       "new_text": "a + b"}).ok
    assert (tmp_path / "calculator.py").read_text() == "return a + b\n"
    assert (tmp_path / "tests.py").read_text() == "assert True\n"
    assert not (tmp_path / "new.py").exists()


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
    assert compacted[1]["role"] == "system"
    assert "read_file" in compacted[1]["content"]
    assert compacted[2]["content"] == "Fix calculator"
    assert manager.estimate(compacted) < 500
    compacted.extend([{"role": "assistant", "content": "More work"},
                      {"role": "tool", "content": "y" * 4_000}])
    again, changed = manager.compact(compacted)
    assert changed
    assert again[1]["role"] == "system"
    assert again[1]["content"].startswith("Earlier steps (compressed")
    assert again[2]["content"] == "Fix calculator"


def test_docker_executor_restricts_network_and_resources(tmp_path: Path, monkeypatch) -> None:
    captured = []
    (tmp_path / ".env").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".env.production").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("SECRET=test", encoding="utf-8")
    (tmp_path / ".npmrc").write_text("TOKEN=test", encoding="utf-8")
    (tmp_path / "app.py").write_text("print(1)", encoding="utf-8")

    def fake_run(argv, **kwargs):
        captured.append(argv)
        if kwargs.get("stdin") is not None:
            with tarfile.open(fileobj=kwargs["stdin"]) as archive:
                names = archive.getnames()
            assert "app.py" in names
            assert ".env" not in names
            assert ".env.production" not in names
            assert ".npmrc" not in names
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
        seen.append((url, key, kwargs["payload"]))
        if key == "test-a":
            raise ProviderHTTPError("unavailable", status_code=503)
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {
                "name": "read_file", "arguments": '{"path":"app.py"}'}}]}}]}

    monkeypatch.setattr("selfforge_agent.runtime.model.request_json", fake_request)
    turn = OpenAIChatAdapter(providers).complete([{"role": "assistant", "content": "",
                                                   "reasoning_content": "private reasoning"},
                                                  {"role": "user", "content": "task"}],
                                                  [{"type": "function"}], 5)
    assert turn.provider == "openrouter"
    assert turn.message["tool_calls"][0]["function"]["name"] == "read_file"
    assert len(seen) == 2
    assert seen[0][2]["messages"][0]["reasoning_content"] == "private reasoning"
    assert "reasoning_content" not in seen[1][2]["messages"][0]


def test_deepseek_reasoning_survives_tool_round_trip_and_session_resume(
    tmp_path: Path, monkeypatch
) -> None:
    provider = ProviderConfig(name="deepseek", label="DeepSeek", key="test", model="a",
                              base_url="https://example.com/a", status_url="")
    seen = []

    def fake_request(_url, _key, **kwargs):
        seen.append(kwargs["payload"])
        if len(seen) == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "content": "", "reasoning_content": "think privately",
                "tool_calls": [{"id": "1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"app.py"}'}}]}}]}
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "Done", "reasoning_content": "conclude privately"}}]}

    monkeypatch.setattr("selfforge_agent.runtime.model.request_json", fake_request)
    adapter = OpenAIChatAdapter([provider])
    first = adapter.complete([{"role": "user", "content": "task"}], [{"type": "function"}], 5)
    session = SessionStore(tmp_path)
    session.record_message(first.message)
    session.record_message({"role": "tool", "tool_call_id": "1", "content": "source"})
    history, _ = SessionStore(tmp_path, session.session_id).load()
    second = adapter.complete(history, [], 5)
    assert first.message["reasoning_content"] == "think privately"
    assert seen[1]["messages"][0]["reasoning_content"] == "think privately"
    assert "tools" not in seen[1] and "tool_choice" not in seen[1]
    assert second.message["content"] == "Done"


def test_docker_timeout_forces_container_cleanup(tmp_path: Path, monkeypatch) -> None:
    from selfforge_agent.runtime import execution

    monkeypatch.setattr(execution, "_run", lambda *_args: execution.CommandResult(None, "", True))
    removed = []
    monkeypatch.setattr(execution.subprocess, "run",
                        lambda argv, **_kwargs: removed.append(argv))
    assert DockerExecutor(tmp_path).run("sleep 10", 1).timed_out
    assert removed and removed[0][:3] == ["docker", "rm", "-f"]
