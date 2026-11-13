"""Regression records must remain bounded, sanitized and bound to their source session."""

import json

import pytest
from fastapi.testclient import TestClient
from test_coding_regression_validation import (
    _LocalSnapshotDockerRunner,
    _setup_docker_session,
)

from selfforge_agent.api import app as api_module
from selfforge_agent.coding import regression_validation as reg
from selfforge_agent.runtime.session import SessionStore


def _event(session_id='safe01'):
    return {'status':'confirmed', 'reason':'base_failed_repaired_passed',
            'session_id':session_id, 'base_sha':'a'*40,
            'workspace_sha256':'b'*64, 'test_sha256':'c'*64,
            'base_status':'failed', 'repaired_status':'passed'}


@pytest.mark.parametrize('changes', [
    {'session_id':'other'}, {'base_sha':'bad'}, {'test_sha256':'bad'}, {'reason':'both_passed'},
])
def test_invalid_confirmation_cannot_establish_evidence(tmp_path, changes):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    SessionStore._append(path, {**_event(), **changes})
    assert store.confirmed_regression_snapshot() is None


@pytest.mark.parametrize('tail', [b'{bad}\n', b'{"status":"error"', b'[]\n', b'\xff\n'])
def test_unreadable_latest_event_revokes_previous_confirmation(tmp_path, tail):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    SessionStore._append(path, _event())
    with path.open('ab') as handle:
        handle.write(tail)
    assert store.confirmed_regression_snapshot() is None
    assert store.latest_regression_record() is None


def test_records_are_sanitized_and_symlinks_are_not_read(tmp_path):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    SessionStore._append(path, {**_event(), 'private_error':'SECRET_PRIVATE_BODY'})
    assert 'SECRET_PRIVATE_BODY' not in json.dumps(store.regression_records())
    outside = tmp_path / 'outside.jsonl'
    path.rename(outside)
    path.symlink_to(outside)
    assert store.regression_records() == []
    assert store.confirmed_regression_snapshot() is None


def test_oversized_journal_cannot_reuse_a_confirmation(tmp_path):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    path.write_bytes(b' '*(4*1024*1024) + json.dumps(_event()).encode() + b'\n')
    assert store.latest_regression_record() is None
    assert store.confirmed_regression_snapshot() is None


def test_real_validation_roundtrips_complete_report_through_get(tmp_path, monkeypatch):
    repo, _, manager, workspace, _ = _setup_docker_session(tmp_path, monkeypatch)
    monkeypatch.setattr(reg, 'DockerTestRunner', _LocalSnapshotDockerRunner)
    (repo/'candidate.py').write_text('from calculator import add\ndef test_fix(): assert add(2,3)==5\n')
    report = reg.validate_workspace_regression(repo, workspace.session_id, 'candidate.py',
                                              workspace_manager=manager)
    response = TestClient(api_module.app).get(
        f'/api/code/workspaces/{workspace.session_id}/regression', params={'repository':str(repo)})
    assert response.status_code == 200
    assert response.json() == report.model_dump()


@pytest.mark.parametrize('session_id', ['éclair', 'a'*65])
def test_get_rejects_invalid_session_ids_without_server_error(tmp_path, session_id):
    repo=tmp_path/'repo'
    repo.mkdir()
    (repo/'.git').mkdir()
    response=TestClient(api_module.app).get(
        f'/api/code/workspaces/{session_id}/regression',params={'repository':str(repo)})
    assert response.status_code == 400


def test_get_rejects_fake_git_directory(tmp_path):
    repo=tmp_path/'repo'
    repo.mkdir()
    (repo/'.git').mkdir()
    response=TestClient(api_module.app).get(
        '/api/code/workspaces/safe01/regression',params={'repository':str(repo)})
    assert response.status_code == 400


def test_later_valid_record_recovers_after_corrupt_history(tmp_path):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    path.write_bytes(b'{bad}\n')
    SessionStore._append(path, _event())
    assert len(store.regression_records()) == 1
    assert store.confirmed_regression_snapshot() == 'b'*64


def test_delivery_rejects_regression_from_another_base(tmp_path, monkeypatch):
    from selfforge_agent.coding.worktree import git
    from selfforge_agent.runtime.delivery import _verified_regression
    from selfforge_agent.runtime.fingerprint import workspace_fingerprint

    _, _, _, workspace, store = _setup_docker_session(tmp_path, monkeypatch)
    git(workspace.path, 'add', 'calculator.py')
    git(workspace.path, 'commit', '-qm', 'repair')
    sha = git(workspace.path, 'rev-parse', 'HEAD').strip()
    path = store.path.with_name(f'{store.session_id}.regression.jsonl')
    SessionStore._append(path, {**_event(store.session_id),
        'base_sha':'f'*40, 'workspace_sha256':workspace_fingerprint(workspace.path)})
    assert _verified_regression(workspace, store, sha) is False


@pytest.mark.parametrize('changes', [{'base_status':'passed'}, {'repaired_status':'failed'}])
def test_legacy_confirmation_requires_consistent_flat_statuses(tmp_path, changes):
    store = SessionStore(tmp_path, 'safe01')
    path = store.path.with_name('safe01.regression.jsonl')
    SessionStore._append(path, {**_event(), **changes})
    assert store.confirmed_regression_snapshot() is None
