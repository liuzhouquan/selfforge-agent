import json
import time
from multiprocessing import get_context

import pytest
from fastapi.testclient import TestClient

from selfforge_agent import providers
from selfforge_agent.api.app import app
from selfforge_agent.integrations import model_http
from selfforge_agent.integrations.model_http import ProviderHTTPError


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
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(path))
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
    assert path.with_name("providers.json.lock").stat().st_mode & 0o777 == 0o600
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
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(tmp_path / "providers.json"))
    client = TestClient(app)
    for url in ["http://remote.example/v1", "https://user:pass@example.com/v1",
                "https://example.com/v1?token=secret"]:
        response = client.post("/api/providers/config", json={
            "provider": "openrouter", "base_url": url, "model": "test/model",
        })
        assert response.status_code == 400
    assert not (tmp_path / "providers.json").exists()


def test_concurrent_provider_saves_preserve_both_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    original_settings = providers._settings

    def slow_settings() -> dict[str, dict[str, str]]:
        settings = original_settings()
        time.sleep(0.1)
        return settings

    monkeypatch.setattr(providers, "_settings", slow_settings)
    context = get_context("fork")
    start = context.Barrier(2)

    def save(provider: str, model: str) -> None:
        start.wait(timeout=5)
        providers.save_provider_settings(
            provider, api_key=f"{provider}-secret",
            base_url=f"https://{provider}.example/v1", model=model,
        )

    workers = [context.Process(target=save, args=("deepseek", "deepseek-chat")),
               context.Process(target=save, args=("openrouter", "openai/gpt-4.1-mini"))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
    assert [worker.exitcode for worker in workers] == [0, 0]

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"deepseek", "openrouter"}
    assert saved["deepseek"]["model"] == "deepseek-chat"
    assert saved["openrouter"]["model"] == "openai/gpt-4.1-mini"


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
