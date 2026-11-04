"""Malformed bounded lock input must remain a client error."""

from fastapi.testclient import TestClient

from selfforge_agent.api.app import app


def test_deep_poetry_toml_returns_client_error(tmp_path):
    nested = "nested=" + "[" * 1500 + "0" + "]" * 1500
    (tmp_path / "poetry.lock").write_text(
        nested + '\n[[package]]\nname="demo"\nversion="1"\n'
        + 'files=[{hash="sha256:' + "a" * 64 + '"}]\n')
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/sandbox/plan", json={"repository": str(tmp_path)})
    assert response.status_code == 400
