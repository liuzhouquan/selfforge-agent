"""Calibrated model pricing and monetary cost estimation."""

from pydantic import BaseModel, Field

from selfforge_agent.providers import TokenUsage

# Rates per 1,000,000 tokens (USD)
CALIBRATED_RATES: dict[str, tuple[float, float]] = {
    # model_key: (prompt_per_million, completion_per_million)
    "deepseek-chat": (0.14, 0.28),
    "deepseek-coder": (0.14, 0.28),
    "deepseek-flash": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.50, 10.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "llama-3.3-70b-instruct": (0.35, 0.40),
}

_MODEL_ALIASES: dict[str, str] = {
    "openai/gpt-4o-mini": "gpt-4o-mini",
    "openai/gpt-4.1-mini": "gpt-4.1-mini",
    "openai/gpt-4o": "gpt-4o",
    "openai/gpt-4.1": "gpt-4.1",
    "anthropic/claude-3-5-sonnet": "claude-3-5-sonnet",
    "anthropic/claude-3.5-sonnet": "claude-3-5-sonnet",
    "deepseek/deepseek-chat": "deepseek-chat",
    "deepseek/deepseek-coder": "deepseek-coder",
    "deepseek/deepseek-reasoner": "deepseek-reasoner",
    "meta-llama/llama-3.3-70b-instruct": "llama-3.3-70b-instruct",
}


class MonetaryCost(BaseModel):
    currency: str = "USD"
    prompt_cost_usd: float = Field(ge=0.0)
    completion_cost_usd: float = Field(ge=0.0)
    total_cost_usd: float = Field(ge=0.0)
    calibrated: bool = True


def normalize_model_name(model: str) -> str:
    cleaned = model.strip().lower()
    if cleaned in _MODEL_ALIASES:
        return _MODEL_ALIASES[cleaned]
    # Check if suffix after slash is in calibrated rates
    if "/" in cleaned:
        tail = cleaned.split("/", 1)[1]
        if tail in CALIBRATED_RATES:
            return tail
    return cleaned


def estimate_monetary_cost(model: str, usage: TokenUsage | None) -> MonetaryCost | None:
    if usage is None:
        return None
    key = normalize_model_name(model)
    rates = CALIBRATED_RATES.get(key)
    if rates is None:
        return None
    prompt_rate, completion_rate = rates
    prompt_cost = round((usage.prompt_tokens / 1_000_000.0) * prompt_rate, 6)
    completion_cost = round((usage.completion_tokens / 1_000_000.0) * completion_rate, 6)
    total_cost = round(prompt_cost + completion_cost, 6)
    return MonetaryCost(
        currency="USD",
        prompt_cost_usd=prompt_cost,
        completion_cost_usd=completion_cost,
        total_cost_usd=total_cost,
        calibrated=True,
    )


def format_monetary_cost(cost: MonetaryCost | None) -> str:
    if cost is None:
        return "uncalibrated"
    return f"${cost.total_cost_usd:.6f} {cost.currency} (prompt: ${cost.prompt_cost_usd:.6f}, completion: ${cost.completion_cost_usd:.6f})"
