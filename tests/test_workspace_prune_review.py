from conftest import git
from test_workspace_prune import _init_repo

from selfforge_agent.api.coding_jobs import CodingJobManager
from selfforge_agent.runtime.workspace import CodingWorkspaceManager


def commit(repo, message):
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-am", message)


def test_web_owned_workspace_survives_prune_with_configured_lock_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SELFFORGE_LOCKS_ROOT", str(tmp_path / "locks"))
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    ws = manager.create(repo, "active")
    jobs = CodingJobManager(root=tmp_path / "jobs")
    with jobs.workspace_guard(ws.path):
        report = manager.prune(repo)
    assert report["skipped_locked"] == ["active"]
    assert ws.path.is_dir()


def test_ignored_user_inputs_survive_audit_and_prune(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("*.private\n")
    git(repo, "add", ".gitignore"); commit(repo, "ignore private data")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    ws = manager.create(repo, "private")
    data = ws.path / "secret.private"
    data.write_text("private fixture")
    assert manager.audit(repo)[0]["is_dirty"]
    assert manager.prune(repo)["skipped_dirty"] == ["private"]
    assert data.read_text() == "private fixture"


def test_prune_rechecks_merge_state_under_lock(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    manager = CodingWorkspaceManager(tmp_path / "workspaces")
    ws = manager.create(repo, "changed")
    audit = manager.audit
    def race(*args, **kwargs):
        result = audit(*args, **kwargs)
        (ws.path / "app.py").write_text("NEW = 2\n")
        commit(ws.path, "new unmerged commit after audit")
        return result
    monkeypatch.setattr(manager, "audit", race)
    assert manager.prune(repo)["skipped_unmerged"] == ["changed"]
    assert ws.path.is_dir()
