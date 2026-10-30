"""Successful tests must describe the current workspace, including after resume."""

import json
import os
import subprocess

import pytest
from test_runtime_recovery import FINAL, Turns, call, verified_session

from selfforge_agent.runtime import agent
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.fingerprint import WorkspaceSnapshotError
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.verification.runner import DockerTestRunner


@pytest.mark.parametrize("change", ["source", "test", "new_file", "delete", "mode"])
def test_resume_revokes_evidence_after_external_change(tmp_path, change):
    session, registry = verified_session(tmp_path)
    source = tmp_path / "app.py"
    if change == "source":
        info = source.stat()
        source.write_text("value = 0\n")  # Same length and timestamps must still invalidate.
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
    elif change == "test":
        (tmp_path / "tests/test_app.py").write_text("def test_other(): assert True\n")
    elif change == "new_file":
        (tmp_path / "config.toml").write_text("changed = true\n")
    elif change == "delete":
        source.unlink()
    else:
        source.chmod(0o755)
    reopened = SessionStore(tmp_path, session.session_id)
    result = CodingAgent(Turns(FINAL), registry, reopened).run(resume=True)
    assert result.status == "unverified"
    assert not reopened.verification_passed()


def test_external_write_during_final_model_call_cannot_complete(tmp_path):
    session, registry = verified_session(tmp_path)

    class Editor(Turns):
        def complete(self, messages, tools, timeout):
            (tmp_path / "app.py").write_text("value = 0\n")
            return super().complete(messages, tools, timeout)

    result = CodingAgent(Editor(FINAL), registry, session).run(resume=True)
    assert result.status == "unverified" and not session.verification_passed()


def test_test_side_effects_cannot_verify_different_source(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text(
        "from pathlib import Path\nfrom app import value\n"
        "def test_value():\n    assert value == 1\n"
        "    Path('app.py').write_text('value = 0\\n')\n")
    session = SessionStore(tmp_path)
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
    result = CodingAgent(Turns(call("run_tests", {}), FINAL), tools, session).run("Verify app")
    assert result.status == "unverified" and not session.verification_passed()
    assert "workspace" in session.load()[0][-2]["content"].lower()


def test_unchanged_workspace_and_generated_cache_remain_resumable(tmp_path):
    session, tools = verified_session(tmp_path)
    (tmp_path / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / "__pycache__/app.pyc").write_bytes(b"generated")
    (tmp_path / ".pytest_cache/generated").write_text("cache")
    result = CodingAgent(Turns(FINAL), tools, session).run(resume=True)
    assert result.status == "completed" and session.verification_passed()


def test_retest_establishes_evidence_for_new_workspace(tmp_path):
    session, tools = verified_session(tmp_path)
    (tmp_path / "config.toml").write_text("new = true\n")
    result = CodingAgent(Turns(call("run_tests", {}), FINAL), tools, session).run(resume=True)
    assert result.status == "completed" and session.verification_passed()


def test_unreadable_snapshot_revokes_saved_evidence(tmp_path, monkeypatch):
    session, tools = verified_session(tmp_path)

    def unreadable(*_args, **_kwargs):
        raise WorkspaceSnapshotError("Workspace fingerprint could not read a complete tree")

    monkeypatch.setattr(agent, "workspace_fingerprint", unreadable)
    result = CodingAgent(Turns(FINAL), tools, session).run(resume=True)
    assert result.status == "unverified" and not session.verification_passed()


def test_saved_evidence_cannot_be_reused_in_identical_other_workspace(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    session, _ = verified_session(first)
    other = tmp_path / "other"
    (other / "tests").mkdir(parents=True)
    for name in ("app.py", "tests/test_app.py"):
        (other / name).write_bytes((first / name).read_bytes())
    tools = ToolRegistry(other, HostExecutor(other, trusted=True))
    result = CodingAgent(Turns(FINAL), tools, session).run(resume=True)
    assert result.status == "unverified" and not session.verification_passed()


@pytest.mark.parametrize("marker,digest", [
    ("junit-v1", None), ("junit-v1", "1" * 64),
    ("junit-workspace-v2", None), ("junit-workspace-v2", "not-a-hash"),
])
def test_unbound_or_malformed_success_cannot_restore_verification(tmp_path, marker, digest):
    session = SessionStore(tmp_path)
    session.record_message({"role": "user", "content": "Verify app"})
    with session.path.open("a") as handle:
        handle.write(json.dumps({"type": "verification", "passed": True,
                                 "evidence": marker, "workspace_sha256": digest}) + "\n")
    result = CodingAgent(Turns(FINAL), ToolRegistry(tmp_path), session).run(resume=True)
    assert result.status == "unverified" and not session.verification_passed()


def test_cannot_persist_new_success_without_a_valid_fingerprint(tmp_path):
    session = SessionStore(tmp_path)
    with pytest.raises(ValueError, match="fingerprint"):
        session.record_verification(True)
    for digest in ("", "1" * 63, "G" * 64, 42):
        with pytest.raises(ValueError, match="fingerprint"):
            session.record_verification(True, workspace_sha256=digest)
    assert not session.path.exists()


@pytest.mark.integration
def test_real_docker_verification_is_revoked_after_external_edit(tmp_path):
    for probe in (["docker", "info"], ["docker", "image", "inspect",
                                      "selfforge-agent-sandbox:latest"]):
        try:
            result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker daemon unavailable")
        if result.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text(
        "from app import value\ndef test_value(): assert value == 1\n")
    session = SessionStore(tmp_path)
    executor = DockerExecutor(tmp_path)
    tools = ToolRegistry(tmp_path, executor)
    initial = CodingAgent(Turns(call("run_tests", {}), FINAL), tools, session).run("Verify app")
    assert initial.status == "completed" and session.verification_passed()
    assert session.environment()["image_id"] == executor.image
    (tmp_path / "app.py").write_text("value = 0\n")
    resumed = CodingAgent(Turns(FINAL), tools, session).run(resume=True)
    assert resumed.status == "unverified" and not session.verification_passed()
    independent = DockerTestRunner(executor.image).run(tmp_path, test_path="tests", timeout=20)
    assert independent.failed == 1 and independent.status == "failed"
