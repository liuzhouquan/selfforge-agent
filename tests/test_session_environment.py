"""A resumed Coding session must execute against its original environment."""

import json
import re
import subprocess
import sys
from threading import Event
from uuid import uuid4

import pytest

from selfforge_agent import cli
from selfforge_agent.runtime import environment
from selfforge_agent.runtime import execution as execution_module
from selfforge_agent.runtime.agent import SYSTEM_PROMPT, CodingAgent
from selfforge_agent.runtime.execution import CommandResult, DockerExecutor, HostExecutor
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64


class Model:
    def __init__(self, *, test=False):
        self.test = test
        self.calls = 0

    def complete(self, _messages, _tools, _timeout):
        self.calls += 1
        if self.test:
            self.test = False
            return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
                "id": "verify", "type": "function", "function": {
                    "name": "run_tests", "arguments": '{"path":"tests"}',
                },
            }]}, "tool_calls", "fixture", "scripted", None)
        return ModelTurn({"role": "assistant", "content": "Finished."},
                         "stop", "fixture", "scripted", None)


class RecordingDocker(DockerExecutor):
    def __init__(self, root, image=None):
        super().__init__(root, image)
        self.executed = []

    def run(self, command, timeout):
        self.executed.append(self.image)
        return CommandResult(0, "1 passed")

    def run_with_artifact(self, command, timeout, name):
        # Also exercises this test on branches using the stricter JUnit path.
        return self.run(command, timeout), b'<testsuite tests="1" />'


def prepare(root, monkeypatch):
    (root / "tests").mkdir()
    images = {"project:tag": A, A: A, B: B, "selfforge-agent-sandbox:latest": A}
    inspected = []

    def resolve(reference, timeout):
        assert 0 < timeout <= 10
        inspected.append(reference)
        if reference not in images:
            raise ValueError("image unavailable locally")
        return images[reference]

    monkeypatch.setattr(environment, "resolve_docker_image", resolve)
    session = SessionStore(root)
    executor = RecordingDocker(root, "project:tag")
    first = CodingAgent(Model(test=True), ToolRegistry(root, executor), session,
                        max_steps=1).run("Verify environment")
    assert first.status == "max_steps" and executor.executed == [A]
    assert session.verification_passed()
    return session, images, inspected


def test_resume_reuses_original_id_after_tag_changes(tmp_path, monkeypatch):
    session, images, inspected = prepare(tmp_path, monkeypatch)
    images["project:tag"] = B
    reopened = SessionStore(tmp_path, session.session_id)
    executor = RecordingDocker(tmp_path)
    result = CodingAgent(Model(test=True), ToolRegistry(tmp_path, executor), reopened).run(resume=True)
    assert result.status == "completed" and executor.executed == [A]
    assert inspected == ["project:tag", A]
    assert reopened.environment() == {"version": 1, "sandbox": "docker", "image_id": A}
    assert [e["event"] for e in reopened.trace_events()].count("execution_environment") == 1
    assert reopened.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("problem", ["changed", "missing", "host"])
def test_resume_rejects_changes_or_missing_images_before_model(tmp_path, monkeypatch, problem):
    session, images, _ = prepare(tmp_path, monkeypatch)
    images["project:tag"] = B
    executor = RecordingDocker(tmp_path, "project:tag" if problem == "changed" else None)
    if problem == "missing":
        images.pop(A)
    elif problem == "host":
        executor = HostExecutor(tmp_path, trusted=True)
    model = Model(test=True)
    result = CodingAgent(model, ToolRegistry(tmp_path, executor), session).run(resume=True)
    assert result.status == "failed" and model.calls == 0
    assert session.environment()["image_id"] == A
    if isinstance(executor, RecordingDocker):
        assert executor.executed == []


def test_host_session_cannot_silently_switch_to_docker(tmp_path):
    session = SessionStore(tmp_path)
    first = CodingAgent(Model(), ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True)),
                        session).run("Inspect")
    assert first.status == "unverified"
    model = Model()
    result = CodingAgent(model, ToolRegistry(tmp_path, RecordingDocker(tmp_path)),
                         session).run(resume=True)
    assert result.status == "failed" and model.calls == 0


def test_legacy_binding_requires_fresh_test_and_recovers_torn_event(tmp_path, monkeypatch):
    session = SessionStore(tmp_path)
    session.record_message({"role": "system", "content": SYSTEM_PROMPT})
    session.record_message({"role": "user", "content": "Verify environment"})
    session.record_verification(True)
    with session.path.open("ab") as handle:
        handle.write(b'{"type":"execution_environment"')
    monkeypatch.setattr(environment, "resolve_docker_image", lambda *_args: A)
    tools = ToolRegistry(tmp_path, RecordingDocker(tmp_path))
    result = CodingAgent(Model(), tools, session).run(resume=True)
    assert result.status == "unverified" and not session.verification_passed()
    (tmp_path / "tests").mkdir()
    retested = CodingAgent(Model(test=True), tools, session).run(resume=True)
    assert retested.status == "completed"


@pytest.mark.parametrize("value", [
    {}, {"version": True, "sandbox": "docker", "image_id": A},
    {"version": 1, "sandbox": [], "image_id": A},
    {"version": 1, "sandbox": "docker", "image_id": "project:tag"},
    {"version": 1, "sandbox": "host", "image_id": A},
])
def test_corrupt_environment_records_are_rejected(tmp_path, value):
    session = SessionStore(tmp_path)
    session.path.write_text(json.dumps({"type": "execution_environment", "environment": value}) + "\n")
    with pytest.raises(ValueError, match="Invalid session execution environment"):
        session.environment()


def test_environment_is_immutable_and_conflicting_logs_fail(tmp_path):
    session = SessionStore(tmp_path)
    session.record_environment({"version": 1, "sandbox": "docker", "image_id": A})
    with pytest.raises(ValueError, match="Cannot change"):
        session.record_environment({"version": 1, "sandbox": "docker", "image_id": B})
    with session.path.open("a") as handle:
        handle.write(json.dumps({"type": "execution_environment", "environment": {
            "version": 1, "sandbox": "docker", "image_id": B,
        }}) + "\n")
    with pytest.raises(ValueError, match="Conflicting"):
        session.environment()


def test_inspection_timeout_and_storage_failure_stop_before_model(tmp_path, monkeypatch):
    def timeout(*_args):
        raise TimeoutError("inspection timed out")
    session = SessionStore(tmp_path)
    monkeypatch.setattr(environment, "resolve_docker_image", timeout)
    model = Model()
    result = CodingAgent(model, ToolRegistry(tmp_path, RecordingDocker(tmp_path)), session).run("Check")
    assert result.status == "timed_out" and model.calls == 0
    monkeypatch.setattr(environment, "resolve_docker_image", lambda *_args: A)
    def storage(_environment):
        raise OSError("session storage failed")
    monkeypatch.setattr(session, "record_environment", storage)
    result = CodingAgent(model, ToolRegistry(tmp_path, RecordingDocker(tmp_path)),
                         session).run(resume=True)
    assert result.status == "failed" and model.calls == 0


def test_cancelled_agent_does_not_inspect_image(tmp_path, monkeypatch):
    cancel = Event()
    cancel.set()
    monkeypatch.setattr(environment, "resolve_docker_image",
                        lambda *_args: pytest.fail("No inspection after cancellation"))
    result = CodingAgent(Model(), ToolRegistry(tmp_path, RecordingDocker(tmp_path)),
                         SessionStore(tmp_path), cancel=cancel).run("Check")
    assert result.status == "cancelled"


@pytest.mark.parametrize("command_result,error", [
    (CommandResult(0, A), None),
    (CommandResult(0, "project:tag"), ValueError),
    (CommandResult(1, A), ValueError),
    (CommandResult(None, A), ValueError),
    (CommandResult(None, "", True), TimeoutError),
])
def test_image_resolution_requires_local_success_and_full_id(monkeypatch, command_result, error):
    def inspect(argv, cwd, timeout):
        assert argv == ["docker", "image", "inspect", "--format", "{{.Id}}", "project:tag"]
        assert cwd is None and timeout == 3
        return command_result
    monkeypatch.setattr(execution_module, "_run", inspect)
    if error is None:
        assert execution_module.resolve_docker_image("project:tag", 3) == A
    else:
        with pytest.raises(error):
            execution_module.resolve_docker_image("project:tag", 3)


def test_cli_resume_omits_image_and_preserves_original_id(
    tmp_path, demo_repo, monkeypatch, capsys,
):
    repo, _, _ = demo_repo
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    resolved = {"project:tag": A, A: A}
    monkeypatch.setattr(environment, "resolve_docker_image", lambda ref, _timeout: resolved[ref])
    executors = []
    def executor(root, image=None):
        instance = RecordingDocker(root, image)
        executors.append(instance)
        return instance
    model = Model(test=True)
    monkeypatch.setattr(cli, "DockerExecutor", executor)
    monkeypatch.setattr(cli, "OpenAIChatAdapter", lambda _providers: model)
    prefix = ["selfforge-agent", "code", "--repo", str(repo), "--workspace", "direct"]
    monkeypatch.setattr(sys, "argv", [*prefix, "--task", "Verify environment", "--image",
                                      "project:tag", "--max-steps", "1"])
    assert cli.main() == 1
    session_id = re.search(r"Session (\w+):", capsys.readouterr().out).group(1)
    resolved["project:tag"] = B
    monkeypatch.setattr(sys, "argv", [*prefix, "--resume", session_id])
    assert cli.main() == 0
    assert executors[-1].image == A and not executors[-1].image_explicit
    assert "completed" in capsys.readouterr().out


@pytest.mark.integration
def test_real_resume_ignores_retagged_image(tmp_path):
    for probe in (["docker", "info"], ["docker", "image", "inspect", "selfforge-agent-sandbox:latest"]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    tag = "selfforge-resume-test:" + uuid4().hex
    container = "selfforge-resume-test-" + uuid4().hex
    try:
        subprocess.run(["docker", "tag", "selfforge-agent-sandbox:latest", tag],
                       timeout=10, check=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests/test_environment.py").write_text(
            "import os\ndef test_environment():\n"
            "    assert 'SELFFORGE_ENV_CHANGED' not in os.environ\n")
        session = SessionStore(tmp_path)
        executor = DockerExecutor(tmp_path, tag)
        first = CodingAgent(Model(test=True), ToolRegistry(tmp_path, executor), session,
                            max_steps=1).run("Verify environment")
        assert first.status == "max_steps"
        saved_id = session.environment()["image_id"]
        subprocess.run(["docker", "create", "--name", container, saved_id, "true"],
                       capture_output=True, timeout=10, check=True)
        subprocess.run(["docker", "commit", "--change", "ENV SELFFORGE_ENV_CHANGED=yes",
                        container, tag], capture_output=True, timeout=15, check=True)
        changed_id = subprocess.check_output(["docker", "image", "inspect", "--format",
                                              "{{.Id}}", tag], text=True).strip()
        assert changed_id != saved_id
        reopened = SessionStore(tmp_path, session.session_id)
        executor = DockerExecutor(tmp_path)
        resumed = CodingAgent(Model(test=True), ToolRegistry(tmp_path, executor), reopened).run(resume=True)
        assert resumed.status == "completed" and executor.image == saved_id
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10, check=False)
        subprocess.run(["docker", "image", "rm", tag], capture_output=True, timeout=10, check=False)
