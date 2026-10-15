import pytest

from verified_pr_agent import providers
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
