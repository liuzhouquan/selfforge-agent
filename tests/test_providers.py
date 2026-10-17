import pytest
from fastapi.testclient import TestClient

from verified_pr_agent import providers
from verified_pr_agent.api.app import app
from verified_pr_agent.integrations import model_http
from verified_pr_agent.integrations.model_http import ProviderHTTPError


def test_provider_order_and_explicit_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    assert [item.name for item in providers.route_providers("auto", True)] == [
        "deepseek", "openrouter"
    ]
    assert [item.name for item in providers.route_providers("openrouter", True)] == [
        "openrouter", "deepseek"
    ]
    assert [item.name for item in providers.route_providers("deepseek", False)] == ["deepseek"]
    assert providers.route_providers("rules", True) == []


def test_provider_check_classifies_invalid_key_and_empty_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    deepseek, openrouter = providers.configured_providers()

    def invalid_key(*_args: object, **_kwargs: object) -> dict:
        raise ProviderHTTPError("Provider returned HTTP 401", status_code=401)

    monkeypatch.setattr(providers, "request_json", invalid_key)
    assert providers.check_provider(deepseek).status == "invalid_key"
    monkeypatch.setattr(
        providers, "request_json", lambda *_args, **_kwargs: {"data": {"limit_remaining": 0}}
    )
    assert providers.check_provider(openrouter).status == "insufficient_credits"
    monkeypatch.setattr(
        providers, "request_json", lambda *_args, **_kwargs: {"is_available": False}
    )
    assert providers.check_provider(deepseek).status == "insufficient_credits"


def test_settings_save_and_discover_models_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setenv("VPA_PROVIDER_CONFIG_PATH", str(path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = TestClient(app)
    before = client.get("/api/config").json()["providers"]
    assert [item["base_url"] for item in before] == [
        "https://api.deepseek.com", "https://openrouter.ai/api/v1"
    ]
    response = client.post("/api/providers/config", json={
        "provider": "deepseek", "api_key": "local-secret", "base_url": "https://api.deepseek.com/",
        "model": "deepseek-flash",
    })
    assert response.status_code == 200
    assert "local-secret" not in response.text
    assert path.stat().st_mode & 0o777 == 0o600
    assert providers.configured_providers()[0].key == "local-secret"

    requested = []

    def fake_request(url: str, key: str, **_kwargs: object) -> dict:
        requested.append((url, key))
        return {"data": [{"id": "deepseek-reasoner", "name": "Reasoner"},
                         {"id": "deepseek-flash", "name": "Flash"}]}

    monkeypatch.setattr(providers, "request_json", fake_request)
    models = client.get("/api/providers/deepseek/models")
    assert models.status_code == 200
    assert [item["id"] for item in models.json()] == ["deepseek-flash", "deepseek-reasoner"]
    assert requested == [("https://api.deepseek.com/models", "local-secret")]
    assert "local-secret" not in client.get("/api/config").text

    response = client.post("/api/providers/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-reasoner",
    })
    assert response.status_code == 200
    assert providers.configured_providers()[0].key == "local-secret"
    assert providers.configured_providers()[0].model == "deepseek-reasoner"


def test_provider_settings_reject_unsafe_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("VPA_PROVIDER_CONFIG_PATH", str(tmp_path / "providers.json"))
    client = TestClient(app)
    for url in ["http://remote.example/v1", "https://user:pass@example.com/v1",
                "https://example.com/v1?token=secret"]:
        response = client.post("/api/providers/config", json={
            "provider": "openrouter", "base_url": url, "model": "test/model",
        })
        assert response.status_code == 400
    assert not (tmp_path / "providers.json").exists()


def test_buffered_proxy_response_with_closed_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    class ClosedSocket:
        def settimeout(self, _timeout: float) -> None:
            raise OSError("Bad file descriptor")

    class BufferedResponse:
        fp = type("File", (), {"raw": type("Raw", (), {"_sock": ClosedSocket()})()})()

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read1(self, _limit: int) -> bytes:
            if not hasattr(self, "read"):
                self.read = True
                return b'{"data": []}'
            return b""

    monkeypatch.setattr(model_http, "urlopen", lambda *_args, **_kwargs: BufferedResponse())
    assert model_http.request_json("https://example.com/models", "test-key") == {"data": []}
