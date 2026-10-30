"""Local Web coding tasks use the same runtime, sessions, and execution gates as CLI."""

import json
import subprocess
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.agent import AgentResult
from selfforge_agent.runtime.model import ModelTurn
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Initial"], check=True)
    return repo


def _call(name: str, args: dict, number: int) -> ModelTurn:
    return ModelTurn({"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call-{number}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}, "tool_calls", "fixture", "scripted", None)


class ScriptedModel:
    def __init__(self) -> None:
        self.turns = [
            _call("search_code", {"pattern": "def add"}, 1),
            _call("read_file", {"path": "calculator.py"}, 2),
            _call("edit_file", {"path": "calculator.py", "old_text": "return a - b",
                                "new_text": "return a + b"}, 3),
            _call("run_tests", {"path": "tests"}, 4),
            ModelTurn({"role": "assistant", "content": "Addition fixed and tested."},
                      "stop", "fixture", "scripted", None),
        ]

    def complete(self, _messages: list[dict], _tools: list[dict], _timeout: float) -> ModelTurn:
        return self.turns.pop(0)


def _finished(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        result = client.get(f"/api/code/jobs/{job_id}")
        assert result.status_code == 200
        data = result.json()
        if data["state"] not in {"queued", "running"}:
            return data
        time.sleep(0.05)
    pytest.fail("Coding job did not finish")


def test_web_coding_uses_selected_dependency_image(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    selected = []

    def run(agent, _task, *, resume=False):
        selected.append(agent.tools.executor.image)
        return AgentResult(agent.session.session_id, "unverified", "Image selected", 0, 0)

    monkeypatch.setattr(api_module.CodingAgent, "run", run)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-worktrees"))
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = TestClient(api_module.app)
    response = client.post("/api/code/jobs", json={
        "repository": str(repo), "task": "Check project", "image": "project-deps:locked",
    })
    assert response.status_code == 200
    assert _finished(client, response.json()["job_id"])["state"] == "unverified"
    assert selected == ["project-deps:locked"]
    for invalid in ("", "--privileged", "image name", "x" * 257):
        rejected = client.post("/api/code/jobs", json={
            "repository": str(repo), "task": "Check project", "image": invalid,
        })
        assert rejected.status_code == 422
    assert selected == ["project-deps:locked"]


def test_web_resume_and_history_keep_pinned_image(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    from test_session_environment import A, B, Model, RecordingDocker

    repo = _repo(tmp_path)
    images = {"project:tag": A, A: A}
    model = Model(test=True)
    monkeypatch.setattr("selfforge_agent.runtime.environment.resolve_docker_image",
                        lambda ref, _timeout: images[ref])
    monkeypatch.setattr(api_module, "DockerExecutor", RecordingDocker)
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: model)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-worktrees"))
    jobs_root = tmp_path / "jobs"
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=jobs_root))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = TestClient(api_module.app)
    payload = {"repository": str(repo), "task": "Verify environment", "max_steps": 1,
               "image": "project:tag"}
    first = client.post("/api/code/jobs", json=payload)
    assert first.status_code == 200
    paused = _finished(client, first.json()["job_id"])
    assert paused["state"] == "max_steps" and paused["image_id"] == A
    images["project:tag"] = B
    resumed = client.post("/api/code/jobs", json={"repository": str(repo),
                          "resume_session": paused["session_id"]})
    assert resumed.status_code == 200
    completed = _finished(client, resumed.json()["job_id"])
    assert completed["state"] == "completed" and completed["image_id"] == A
    assert completed["sandbox"] == "docker"
    recovered = CodingJobManager(root=jobs_root).get(completed["job_id"])
    assert recovered["image_id"] == A and recovered["state"] == "completed"
    calls = model.calls
    rejected = client.post("/api/code/jobs", json={"repository": str(repo),
                           "resume_session": paused["session_id"], "image": "project:tag"})
    assert rejected.status_code == 200
    failed = _finished(client, rejected.json()["job_id"])
    assert failed["state"] == "failed" and model.calls == calls
    assert "image differs" in failed["error"]


def test_web_coding_can_resume_and_verify_on_host(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    model = ScriptedModel()
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "app-workspaces")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-worktrees"))
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: model)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    client = TestClient(api_module.app)
    payload = {"repository": str(repo), "task": "Fix addition", "sandbox": "host",
               "trust_local": True, "max_steps": 1}
    first = client.post("/api/code/jobs", json=payload)
    assert first.status_code == 200
    paused = _finished(client, first.json()["job_id"])
    assert paused["state"] == "max_steps"
    second = client.post("/api/code/jobs", json={**payload, "task": "Continue and test",
                                                   "resume_session": paused["session_id"],
                                                   "max_steps": 6})
    assert second.status_code == 200
    done = _finished(client, second.json()["job_id"])
    assert done["state"] == "completed"
    assert done["session_id"] == paused["session_id"]
    assert done["tools_used"] == 3
    assert any(item["event"] == "tool" and item["name"] == "run_tests" and item["ok"]
               for item in done["trace"])
    workspace = Path(done["workspace_path"])
    assert workspace != repo
    assert done["branch"] == f"selfforge/{done['session_id']}"
    assert "return a + b" in (workspace / "calculator.py").read_text()
    assert "return a - b" in (repo / "calculator.py").read_text()
    diff = client.get(f"/api/code/workspaces/{done['session_id']}/diff",
                      params={"repository": str(repo)})
    assert diff.status_code == 200
    assert any(item["path"] == "calculator.py" and "+    return a + b" in item["patch"]
               for item in diff.json()["files"])
    monkeypatch.setattr("selfforge_agent.runtime.workspace_diff.MAX_PATH_LIST_BYTES", 1)
    oversized = client.get(f"/api/code/workspaces/{done['session_id']}/diff",
                           params={"repository": str(repo)})
    assert oversized.status_code == 422
    assert "Too many changed paths" in oversized.json()["detail"]
    assert client.get(f"/api/code/workspaces/{done['session_id']}/diff",
                      params={"repository": str(tmp_path / "other")}).status_code == 404
    assert not (repo / ".selfforge").exists()
    assert api_module._CODING_WORKSPACES.session_root(repo).joinpath(
        ".selfforge", "sessions", f"{done['session_id']}.jsonl").exists()
    assert client.post(f"/api/code/workspaces/{done['session_id']}/remove",
                       json={"repository": str(repo)}).status_code == 409
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "Fix"], check=True)
    removed = client.post(f"/api/code/workspaces/{done['session_id']}/remove",
                          json={"repository": str(repo)})
    assert removed.status_code == 200
    assert not workspace.exists()
    assert subprocess.run(["git", "-C", str(repo), "branch", "--list", done["branch"]],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_web_coding_host_requires_operator_and_request_trust(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    monkeypatch.delenv("SELFFORGE_ENABLE_HOST_TESTS", raising=False)
    client = TestClient(api_module.app)
    payload = {"repository": str(repo), "task": "Fix", "sandbox": "host",
               "trust_local": True}
    assert client.post("/api/code/jobs", json=payload).status_code == 403
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    payload["trust_local"] = False
    assert client.post("/api/code/jobs", json=payload).status_code == 403
    payload.update(sandbox="docker", workspace="direct")
    assert client.post("/api/code/jobs", json=payload).status_code == 403
    assert client.get("/api/code/jobs/unknown").status_code == 404
    assert client.post("/api/code/jobs/unknown/cancel", json={}).status_code == 404
    remote = Request({"type": "http", "client": ("203.0.113.2", 1234),
                      "method": "POST", "path": "/api/code/jobs", "headers": []})
    with pytest.raises(HTTPException) as rejected:
        api_module.start_coding_job(api_module.CodingTaskRequest(
            repository=str(repo), task="Fix"), remote)
    assert rejected.value.status_code == 403


def test_web_direct_mode_requires_confirmation_and_edits_source(monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "app-workspaces")
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: ScriptedModel())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    client = TestClient(api_module.app)
    payload = {"repository": str(repo), "task": "Fix addition", "workspace": "direct",
               "sandbox": "host", "trust_local": True}
    assert client.post("/api/code/jobs", json=payload).status_code == 403
    started = client.post("/api/code/jobs", json={**payload, "confirm_direct": True})
    assert started.status_code == 200
    done = _finished(client, started.json()["job_id"])
    assert done["state"] == "completed"
    assert done["workspace_path"] == str(repo)
    assert done["branch"] == ""
    assert "return a + b" in (repo / "calculator.py").read_text()


def test_web_coding_cancellation_and_workspace_lock(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manager = CodingJobManager(root=tmp_path / "jobs")
    monkeypatch.setattr(api_module, "_CODING_JOBS", manager)
    started = Event()

    class WaitingAgent:
        def __init__(self) -> None:
            self.session = SessionStore(tmp_path / "job-sessions")
            self.tools = SimpleNamespace(root=repo)
            self.cancel = Event()

        def run(self, _task: str, *, resume: bool = False) -> AgentResult:
            assert not resume
            started.set()
            self.cancel.wait(2)
            return AgentResult(self.session.session_id, "cancelled", "", 0, 0)

    job = manager.start(WaitingAgent(), "Wait")
    assert started.wait(1)
    with pytest.raises(ValueError, match="already running"):
        manager.start(WaitingAgent(), "Second task")
    response = TestClient(api_module.app).post(f"/api/code/jobs/{job.job_id}/cancel", json={})
    assert response.status_code == 200
    assert response.json()["cancel_requested"]
    done = _finished(TestClient(api_module.app), job.job_id)
    assert done["state"] == "cancelled"


def test_rejected_web_job_rolls_back_new_worktree(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manager = CodingJobManager(max_active=1, root=tmp_path / "jobs")
    workspaces = CodingWorkspaceManager(tmp_path / "coding-worktrees")
    monkeypatch.setattr(api_module, "_CODING_JOBS", manager)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", workspaces)
    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: ScriptedModel())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    started = Event()

    class WaitingAgent:
        def __init__(self) -> None:
            self.session = SessionStore(tmp_path / "held-session")
            workspace = tmp_path / "other-workspace"
            workspace.mkdir()
            self.tools = SimpleNamespace(root=workspace)
            self.cancel = Event()

        def run(self, _task: str, *, resume: bool = False) -> AgentResult:
            started.set()
            self.cancel.wait(2)
            return AgentResult(self.session.session_id, "cancelled", "", 0, 0)

    held = manager.start(WaitingAgent(), "Hold")
    assert started.wait(1)
    response = TestClient(api_module.app).post("/api/code/jobs", json={
        "repository": str(repo), "task": "Fix addition",
    })
    assert response.status_code == 400
    assert workspaces.list(repo) == []
    assert not subprocess.run(["git", "-C", str(repo), "branch", "--list", "selfforge/*"],
                              capture_output=True, text=True, check=True).stdout.strip()
    manager.cancel(held.job_id)


@pytest.mark.integration
def test_web_coding_defaults_to_real_docker(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    for probe in (["docker", "info"], ["docker", "image", "inspect",
                                      "selfforge-agent-sandbox:latest"]):
        try:
            checked = subprocess.run(probe, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("Docker unavailable")
        if checked.returncode:
            pytest.skip("Docker daemon or sandbox image unavailable")
    repo = _repo(tmp_path)
    monkeypatch.setattr(api_module, "_WORKSPACES", tmp_path / "app-workspaces")
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES",
                        CodingWorkspaceManager(tmp_path / "coding-worktrees"))
    monkeypatch.setattr(api_module, "_CODING_JOBS", CodingJobManager(root=tmp_path / "jobs"))
    class DockerModel(ScriptedModel):
        def __init__(self) -> None:
            super().__init__()
            self.turns.insert(-1, _call("bash", {"command": "python -c 'from pathlib import Path; "
                "Path(\"calculator.py\").write_text(\"def add(a, b):\\n    return a - b\\n\")'"}, 5))

    monkeypatch.setattr(api_module, "OpenAIChatAdapter", lambda _providers: DockerModel())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SELFFORGE_ENABLE_HOST_TESTS", raising=False)
    client = TestClient(api_module.app)
    started = client.post("/api/code/jobs", json={
        "repository": str(repo), "task": "Fix addition and run tests",
    })
    assert started.status_code == 200
    done = _finished(client, started.json()["job_id"])
    assert done["state"] == "completed", done["error"]
    assert "return a + b" in (Path(done["workspace_path"]) / "calculator.py").read_text()
    assert "return a - b" in (repo / "calculator.py").read_text()
    assert done["tools_used"] == 5
    assert any(item["event"] == "tool" and item["name"] == "bash" and item["ok"]
               for item in done["trace"])
