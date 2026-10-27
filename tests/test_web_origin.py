"""Local workbench requests must come from its own browser origin."""

from fastapi.testclient import TestClient

from selfforge_agent.api.app import app


def test_untrusted_host_cannot_reach_local_routes() -> None:
    client = TestClient(app)

    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    assert client.post(
        "/api/code/jobs/no-such-job/cancel",
        headers={"Host": "attacker.example"},
    ).status_code == 400


def test_cross_origin_mutations_are_rejected() -> None:
    client = TestClient(app)
    route = "/api/code/jobs/no-such-job/cancel"

    assert client.post(route, headers={"Origin": "http://attacker.example"}).status_code == 403
    assert client.post(route, headers={"Origin": "null"}).status_code == 403
    assert client.post(route, headers={"Origin": "http://testserver:invalid"}).status_code == 403
    assert client.post(route, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_same_origin_and_non_browser_local_clients_keep_working() -> None:
    client = TestClient(app)
    route = "/api/code/jobs/no-such-job/cancel"

    assert client.post(route, headers={"Origin": "http://testserver",
                                       "Sec-Fetch-Site": "same-origin"}).status_code == 404
    assert client.post(route).status_code == 404
    assert client.post(
        route,
        headers={"Host": "[::1]:8000", "Origin": "http://[::1]:8000"},
    ).status_code == 404
    assert client.post(
        route,
        headers={"Host": "localhost:80", "Origin": "http://localhost"},
    ).status_code == 404
