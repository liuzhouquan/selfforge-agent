"""Server-side model configuration, routing, and key/credit checks."""

import ipaddress
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json

ProviderName = Literal["deepseek", "openrouter", "custom"]
ProviderChoice = Literal["auto", "deepseek", "openrouter", "rules"]


@dataclass(frozen=True)
class ProviderConfig:
    name: ProviderName
    label: str
    key: str
    model: str
    base_url: str
    status_url: str


class ProviderInfo(BaseModel):
    name: ProviderName
    label: str
    configured: bool
    model: str
    base_url: str
    key_source: Literal["local", "environment", "none"]


class AvailableModel(BaseModel):
    id: str
    name: str


_DEFAULTS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com", "deepseek-flash", "DEEPSEEK_API_KEY"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4.1-mini", "OPENROUTER_API_KEY"),
}


def _settings_path() -> Path:
    return Path(os.environ.get("SELFFORGE_PROVIDER_CONFIG_PATH") or
                Path(__file__).resolve().parents[2] / ".local" / "providers.json")


def _settings() -> dict[str, dict[str, str]]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read provider settings: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError("Provider settings must be a JSON object")
    return {
        name: {key: item for key, item in value.items()
               if key in {"api_key", "base_url", "model"} and isinstance(item, str)}
        for name, value in data.items()
        if name in _DEFAULTS and isinstance(value, dict)
    }


def validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        raise ValueError("Base URL must be a plain HTTPS URL without credentials or query")
    if parts.scheme != "https":
        try:
            loopback = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = parts.hostname == "localhost"
        if parts.scheme != "http" or not loopback:
            raise ValueError("Base URL must use HTTPS, except for localhost")
    return url


def save_provider_settings(name: ProviderName, *, api_key: str | None,
                           base_url: str, model: str) -> ProviderInfo:
    if name not in _DEFAULTS:
        raise ValueError("Unknown provider")
    base_url = validate_base_url(base_url)
    model = model.strip()
    if not model or len(model) > 200 or any(char.isspace() for char in model):
        raise ValueError("Model must be a nonempty model ID without spaces")
    if api_key is not None and (len(api_key) > 4096 or "\n" in api_key or "\r" in api_key):
        raise ValueError("Invalid API key")
    settings = _settings()
    current = settings.get(name, {})
    settings[name] = {"base_url": base_url, "model": model}
    if api_key is None:
        if isinstance(current.get("api_key"), str):
            settings[name]["api_key"] = current["api_key"]
    elif api_key:
        settings[name]["api_key"] = api_key.strip()
    path = _settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".providers-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return next(item for item in provider_info() if item.name == name)


class ProviderStatus(BaseModel):
    name: ProviderName
    status: Literal["ready", "invalid_key", "insufficient_credits", "unavailable", "unconfigured"]
    balance: str | None = None
    usage: str | None = None
    error: str | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ModelAttempt(BaseModel):
    provider: ProviderName
    model: str
    status: Literal["success", "error"]
    usage: TokenUsage | None = None
    error: str | None = None


def token_usage_from_response(response: dict[str, object]) -> TokenUsage | None:
    raw = response.get("usage")
    if not isinstance(raw, dict):
        return None
    values = [raw.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
    if not all(type(value) is int and value >= 0 for value in values):
        return None
    return TokenUsage(
        prompt_tokens=values[0], completion_tokens=values[1], total_tokens=values[2]
    )


def configured_providers() -> list[ProviderConfig]:
    settings = _settings()
    result = []
    for name, (label, default_url, default_model, env_key) in _DEFAULTS.items():
        local = settings.get(name, {})
        base_url = validate_base_url(local.get("base_url") or default_url)
        model = local.get("model") or os.environ.get(f"SELFFORGE_{name.upper()}_MODEL") or default_model
        key = local.get("api_key") or os.environ.get(env_key, "")
        result.append(ProviderConfig(name=name, label=label, key=key, model=model,
                                     base_url=base_url, status_url=base_url +
                                     ("/user/balance" if name == "deepseek" else "/key")))
    return result


def provider_info() -> list[ProviderInfo]:
    settings = _settings()
    return [
        ProviderInfo(name=item.name, label=item.label, configured=bool(item.key),
                     model=item.model, base_url=item.base_url,
                     key_source="local" if settings.get(item.name, {}).get("api_key") else
                     "environment" if item.key else "none")
        for item in configured_providers()
    ]


def list_models(item: ProviderConfig) -> list[AvailableModel]:
    if not item.key:
        raise ValueError("Configure an API key before syncing models")
    response = request_json(item.base_url + "/models", item.key, timeout=15,
                            max_bytes=8_000_000)
    data = response.get("data")
    if not isinstance(data, list):
        raise TypeError("Provider returned an invalid models list")
    models = {}
    for entry in data:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            name = entry.get("name")
            models[entry["id"]] = AvailableModel(
                id=entry["id"], name=name if isinstance(name, str) and name else entry["id"])
    return sorted(models.values(), key=lambda model: model.id.lower())


def route_providers(choice: ProviderChoice, fallback: bool) -> list[ProviderConfig]:
    if choice == "rules":
        return []
    configured = [item for item in configured_providers() if item.key]
    if choice == "auto":
        return configured if fallback else configured[:1]
    selected = [item for item in configured if item.name == choice]
    others = [item for item in configured if item.name != choice]
    return selected + others if fallback else selected


def check_provider(item: ProviderConfig) -> ProviderStatus:
    if not item.key:
        return ProviderStatus(name=item.name, status="unconfigured")
    try:
        response = request_json(item.status_url, item.key, timeout=10, max_bytes=32_768)
    except ProviderHTTPError as exc:
        if exc.status_code in {401, 403}:
            status = "invalid_key"
        elif exc.status_code == 402:
            status = "insufficient_credits"
        else:
            status = "unavailable"
        return ProviderStatus(name=item.name, status=status, error=str(exc))
    if item.name == "deepseek":
        balances = response.get("balance_infos")
        amounts = []
        if isinstance(balances, list):
            for balance in balances:
                if isinstance(balance, dict):
                    currency, total = balance.get("currency"), balance.get("total_balance")
                    if isinstance(currency, str) and isinstance(total, str):
                        amounts.append(f"{total} {currency}")
        available = response.get("is_available")
        if not isinstance(available, bool):
            return ProviderStatus(name=item.name, status="unavailable", error="Invalid balance response")
        return ProviderStatus(
            name=item.name, status="ready" if available else "insufficient_credits",
            balance=", ".join(amounts) or None,
        )
    data = response.get("data")
    if not isinstance(data, dict):
        return ProviderStatus(name=item.name, status="unavailable", error="Invalid key response")
    remaining = data.get("limit_remaining")
    if remaining is not None and not isinstance(remaining, (int, float)):
        return ProviderStatus(name=item.name, status="unavailable", error="Invalid credit response")
    budget = data.get("effective_budget")
    budget_remaining = budget.get("remaining_usd") if isinstance(budget, dict) else None
    blocked = (remaining is not None and remaining <= 0) or (
        isinstance(budget_remaining, (int, float)) and budget_remaining <= 0
    )
    used = data.get("usage")
    return ProviderStatus(
        name=item.name, status="insufficient_credits" if blocked else "ready",
        balance=f"${remaining:.4f} remaining" if remaining is not None else "No key credit cap",
        usage=f"${used:.4f} used" if isinstance(used, (int, float)) else None,
    )
