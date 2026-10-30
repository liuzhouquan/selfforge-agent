"""Interrupted writes must never resurrect verification of an older workspace."""

import json
import multiprocessing
import os
import shlex
from pathlib import Path

import pytest

from selfforge_agent.runtime.agent import SYSTEM_PROMPT, CodingAgent
from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry


class Turns:
    def __init__(self, *messages: dict) -> None:
        self.messages = iter(messages)

    def complete(self, _messages, _tools, _timeout):
        message = next(self.messages)
        reason = "tool_calls" if message.get("tool_calls") else "stop"
        return ModelTurn(message, reason, "fixture", "scripted", None)


def call(name: str, arguments: dict) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call-1", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


FINAL = {"role": "assistant", "content": "Finished."}


def verified_session(root: Path, session_id: str | None = None) -> tuple[SessionStore, ToolRegistry]:
    (root / "app.py").write_text("value = 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value == 1\n")
    store = SessionStore(root, session_id)
    registry = ToolRegistry(root, HostExecutor(root, trusted=True))
    result = CodingAgent(Turns(call("run_tests", {"path": "tests"}), FINAL),
                         registry, store, max_steps=2).run("Verify app")
    assert result.status == "completed"
    return store, registry


def _exit_after_edit(root: Path) -> None:
    session, registry = verified_session(root, "abrupt")
    execute = registry.execute

    def interrupted(name, arguments):
        assert execute(name, arguments).ok
        os._exit(73)  # Deliberately bypass exception handlers and finally blocks.

    registry.execute = interrupted
    CodingAgent(Turns(call("edit_file", {
        "path": "app.py", "old_text": "value = 1", "new_text": "value = 2",
    })), registry, session, max_steps=2).run(resume=True)


def test_abrupt_worker_exit_cannot_restore_old_verification(tmp_path: Path) -> None:
    worker = multiprocessing.get_context("spawn").Process(target=_exit_after_edit, args=(tmp_path,))
    worker.start()
    try:
        worker.join(20)
        assert worker.exitcode == 73
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(5)
        worker.close()
    assert (tmp_path / "app.py").read_text() == "value = 2\n"
    reopened = SessionStore(tmp_path, "abrupt")
    assert not reopened.verification_passed()
    result = CodingAgent(Turns(FINAL), ToolRegistry(tmp_path), reopened).run(resume=True)
    assert result.status == "unverified"


@pytest.mark.parametrize("name", ["edit_file", "bash", "run_tests"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_tool_interruption_cannot_restore_old_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, interruption: type[BaseException],
) -> None:
    session, registry = verified_session(tmp_path)
    arguments = {
        "edit_file": {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"},
        "bash": {"command": "python -c " + shlex.quote(
            "from pathlib import Path; Path('app.py').write_text('value = 2\\n')")},
        "run_tests": {"path": "tests"},
    }[name]
    execute = registry.execute

    def interrupted(tool, args):
        outcome = execute(tool, args)
        assert outcome.ok
        raise interruption()  # Tool side effects happened; no result was persisted.

    monkeypatch.setattr(registry, "execute", interrupted)
    agent = CodingAgent(Turns(call(name, arguments)), registry, session, max_steps=2)
    if interruption is KeyboardInterrupt:
        assert agent.run(resume=True).status == "cancelled"
    else:
        with pytest.raises(SystemExit):
            agent.run(resume=True)
    if name != "run_tests":
        assert (tmp_path / "app.py").read_text() == "value = 2\n"
    assert not session.verification_passed()

    reopened = SessionStore(tmp_path, session.session_id)
    resumed = CodingAgent(Turns(FINAL), registry, reopened).run(resume=True)
    assert resumed.status == "unverified"
    assert any("interrupted" in item.get("content", "") for item in reopened.load()[0])

    # Only fresh successful testing can establish evidence again.
    monkeypatch.setattr(registry, "execute", execute)
    (tmp_path / "app.py").write_text("value = 1\n")
    retested = CodingAgent(Turns(call("run_tests", {"path": "tests"}), FINAL),
                           registry, reopened, max_steps=2).run(resume=True)
    assert retested.status == "completed"


@pytest.mark.parametrize("name,expected", [
    ("edit_file", "unverified"), ("bash", "unverified"),
    ("run_tests", "unverified"), ("read_file", "completed"),
])
def test_legacy_pending_calls_revoke_evidence_before_recovery_message(
    tmp_path: Path, name: str, expected: str,
) -> None:
    session = SessionStore(tmp_path)
    session.record_message({"role": "system", "content": SYSTEM_PROMPT})
    session.record_message({"role": "user", "content": "Verify app"})
    session.record_verification(True)
    session.record_message(call(name, {}))  # Legacy interrupted log, no tool response.
    registry = ToolRegistry(tmp_path)
    for _ in range(2):  # Synthetic interruption replies must not hide stale evidence later.
        reopened = SessionStore(tmp_path, session.session_id)
        result = CodingAgent(Turns(FINAL), registry, reopened).run(resume=True)
        assert result.status == expected


def test_new_task_interruption_revokes_previous_task_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, registry = verified_session(tmp_path)
    record = session.record_message

    def interrupted(message):
        record(message)
        raise SystemExit()

    monkeypatch.setattr(session, "record_message", interrupted)
    with pytest.raises(SystemExit):
        CodingAgent(Turns(), registry, session).run("Implement another feature", resume=True)
    reopened = SessionStore(tmp_path, session.session_id)
    result = CodingAgent(Turns(FINAL), registry, reopened).run(resume=True)
    assert result.status == "unverified"


def test_invalidation_storage_failure_prevents_tool_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, registry = verified_session(tmp_path)

    def fail(_passed):
        raise OSError("verification storage unavailable")

    monkeypatch.setattr(session, "record_verification", fail)
    result = CodingAgent(Turns(call("edit_file", {
        "path": "app.py", "old_text": "value = 1", "new_text": "value = 2",
    })), registry, session, max_steps=2).run(resume=True)
    assert result.status == "failed"
    assert (tmp_path / "app.py").read_text() == "value = 1\n"
