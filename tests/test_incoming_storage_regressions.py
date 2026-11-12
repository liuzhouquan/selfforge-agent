import pytest
from test_workspace_storage import create_test_repo

from selfforge_agent.runtime.workspace import CodingWorkspaceManager


@pytest.mark.parametrize('max_files', [1, 0])
def test_incomplete_storage_does_not_certify_quota(tmp_path, max_files):
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path/'worktrees')
    ws = manager.create(repo, 'limit')
    (ws.path/'big').write_bytes(b'x'*1000)
    report = manager.audit_storage(repo, quota_bytes=100000, max_files_per_worktree=max_files)
    assert report['complete'] is False
    assert report['quota_exceeded'] is None
    assert report['workspaces'][0]['prunable'] is False


def test_empty_directories_are_bounded(tmp_path):
    from selfforge_agent.runtime.workspace import _measure_directory
    for i in range(4):
        (tmp_path / str(i)).mkdir()
    result = _measure_directory(tmp_path, max_entries=2)
    assert result['complete'] is False
    assert result['scan_error'] == 'entry_limit'


def test_permission_error_is_not_an_empty_success(tmp_path, monkeypatch):
    from selfforge_agent.runtime import workspace
    def deny(*args):
        raise PermissionError('injected')
    monkeypatch.setattr(workspace.os, 'scandir', deny)
    result = workspace._measure_directory(tmp_path)
    assert result['complete'] is False
    assert result['scan_error'] == 'unreadable_entry'


def test_api_preserves_incomplete_scan_and_unknown_quota(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from selfforge_agent.api.app import app
    from selfforge_agent.runtime import workspace
    monkeypatch.setenv('SELFFORGE_WORKSPACES_ROOT', str(tmp_path/'worktrees'))
    repo = create_test_repo(tmp_path)
    manager = CodingWorkspaceManager(tmp_path/'worktrees')
    manager.create(repo, 'partial')
    def deny(*args, **kwargs):
        return {'total_bytes': 0, 'cache_bytes': 0, 'file_count': 0,
                'complete': False, 'scan_error': 'injected_failure'}
    monkeypatch.setattr(workspace, '_measure_directory', deny)
    response = TestClient(app).get('/api/workspaces/storage', params={
        'repository': str(repo), 'quota_bytes': 100000})
    assert response.status_code == 200
    assert response.json()['complete'] is False
    assert response.json()['quota_exceeded'] is None
    assert response.json()['workspaces'][0]['scan_error'] == 'injected_failure'
