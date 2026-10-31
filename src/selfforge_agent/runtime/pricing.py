"""Estimate spend only from explicitly configured provider/model rate cards."""

import json
import math
import os
from datetime import date

from pydantic import BaseModel, Field

from selfforge_agent.providers import TokenUsage


class ModelRates(BaseModel):
    prompt_per_million: float = Field(ge=0, allow_inf_nan=False)
    completion_per_million: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=500)
    as_of: date


class MonetaryCost(BaseModel):
    currency: str = "USD"
    prompt_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    completion_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    total_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    calibrated: bool = True
    rate_sources: list[str] = Field(default_factory=list)


def estimate_monetary_cost(model: str, usage: TokenUsage | None, *,
                           provider: str | None = None) -> MonetaryCost | None:
    """A model name alone cannot identify endpoint pricing, discounts, or caches."""
    if usage is None or not provider or min(usage.prompt_tokens, usage.completion_tokens) < 0:
        return None
    configured = os.environ.get("SELFFORGE_PRICING_JSON", "")
    if not configured or len(configured) > 16_384:
        return None
    try:
        rates = ModelRates.model_validate(json.loads(configured)[provider][model])
    except (ValueError, TypeError, KeyError):
        return None
    try:
        prompt = usage.prompt_tokens / 1_000_000 * rates.prompt_per_million
        completion = usage.completion_tokens / 1_000_000 * rates.completion_per_million
    except OverflowError:
        return None
    if not math.isfinite(prompt + completion):
        return None
    return MonetaryCost(
        prompt_cost_usd=prompt, completion_cost_usd=completion,
        total_cost_usd=prompt + completion,
        rate_sources=[f"{rates.source} ({rates.as_of.isoformat()})"],
    )


def sum_monetary_costs(costs: list[MonetaryCost | None]) -> MonetaryCost | None:
    """Withhold the total if any call lacks calibrated usage or pricing."""
    if not costs or any(cost is None for cost in costs):
        return None
    known = [cost for cost in costs if cost is not None]
    if not math.isfinite(sum(cost.total_cost_usd for cost in known)):
        return None
    return MonetaryCost(
        prompt_cost_usd=sum(cost.prompt_cost_usd for cost in known),
        completion_cost_usd=sum(cost.completion_cost_usd for cost in known),
        total_cost_usd=sum(cost.total_cost_usd for cost in known),
        calibrated=all(cost.calibrated for cost in known),
        rate_sources=sorted({source for cost in known for source in cost.rate_sources}),
    )


def format_monetary_cost(cost: MonetaryCost | None) -> str:
    if cost is None:
        return "uncalibrated"
    return (f"${cost.total_cost_usd:.6f} {cost.currency} "
            f"(prompt: ${cost.prompt_cost_usd:.6f}, completion: ${cost.completion_cost_usd:.6f})")
