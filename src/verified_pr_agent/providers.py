"""Server-side model configuration, routing, and key/credit checks."""

import os
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from verified_pr_agent.integrations.model_http import ProviderHTTPError, request_json

ProviderName = Literal["deepseek", "openrouter"]
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
    return [
        ProviderConfig(
            name="deepseek", label="DeepSeek", key=os.environ.get("DEEPSEEK_API_KEY", ""),
            model=os.environ.get("VPA_DEEPSEEK_MODEL") or "deepseek-flash",
            base_url="https://api.deepseek.com",
            status_url="https://api.deepseek.com/user/balance",
        ),
        ProviderConfig(
            name="openrouter", label="OpenRouter", key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("VPA_OPENROUTER_MODEL") or "openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api/v1",
            status_url="https://openrouter.ai/api/v1/key",
        ),
    ]


def provider_info() -> list[ProviderInfo]:
    return [
        ProviderInfo(name=item.name, label=item.label, configured=bool(item.key), model=item.model)
        for item in configured_providers()
    ]


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
