"""Estimate spend only from explicitly configured provider/model rate cards."""

from __future__ import annotations

import json
import math
import os
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from selfforge_agent.providers import TokenUsage

MAX_TOKENS_PER_REQUEST = 1_000_000_000
MAX_PRICING_JSON_BYTES = 16_384


class ModelRates(BaseModel):
    prompt_per_million: float = Field(ge=0.0, le=1_000_000.0, allow_inf_nan=False)
    completion_per_million: float = Field(ge=0.0, le=1_000_000.0, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=500)
    as_of: date
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class MonetaryCost(BaseModel):
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    prompt_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    completion_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    total_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    calibrated: bool = True
    rate_sources: list[str] = Field(default_factory=list)


def estimate_monetary_cost(
    model: str, usage: TokenUsage | None, *, provider: str | None = None
) -> MonetaryCost | None:
    """A model name alone cannot identify endpoint pricing, discounts, or caches."""
    if usage is None or not provider or not isinstance(provider, str) or not isinstance(model, str):
        return None
    if any(c in provider or c in model for c in ("\x00", "\r", "\n", "\t")):
        return None

    # Strict integer type checking (blocking boolean and float bypass)
    prompt_toks = getattr(usage, "prompt_tokens", None)
    comp_toks = getattr(usage, "completion_tokens", None)
    if type(prompt_toks) is not int or type(comp_toks) is not int:
        return None
    if prompt_toks < 0 or comp_toks < 0:
        return None
    if prompt_toks > MAX_TOKENS_PER_REQUEST or comp_toks > MAX_TOKENS_PER_REQUEST:
        return None

    configured = os.environ.get("SELFFORGE_PRICING_JSON", "")
    if not configured or len(configured) > MAX_PRICING_JSON_BYTES or "\x00" in configured:
        return None

    try:
        data = json.loads(configured)
        if not isinstance(data, dict):
            return None
        provider_data = data.get(provider)
        if not isinstance(provider_data, dict):
            return None
        card_data = provider_data.get(model)
        if not isinstance(card_data, dict):
            return None
        rates = ModelRates.model_validate(card_data)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return None

    try:
        prompt = prompt_toks / 1_000_000.0 * rates.prompt_per_million
        completion = comp_toks / 1_000_000.0 * rates.completion_per_million
        total = prompt + completion
    except OverflowError:
        return None

    if not math.isfinite(prompt) or not math.isfinite(completion) or not math.isfinite(total):
        return None
    if prompt < 0.0 or completion < 0.0 or total < 0.0:
        return None

    return MonetaryCost(
        currency=rates.currency,
        prompt_cost_usd=prompt,
        completion_cost_usd=completion,
        total_cost_usd=total,
        calibrated=True,
        rate_sources=[f"{rates.source} ({rates.as_of.isoformat()})"],
    )


def sum_monetary_costs(costs: list[MonetaryCost | None]) -> MonetaryCost | None:
    """Withhold the total if any call lacks calibrated usage or pricing, or currencies mismatch."""
    if not costs or any(cost is None for cost in costs):
        return None
    if any(not isinstance(cost, MonetaryCost) for cost in costs):
        return None

    currencies = {cost.currency for cost in costs if cost is not None}
    if len(currencies) != 1:
        return None
    currency = next(iter(currencies))

    try:
        prompt_sum = sum(cost.prompt_cost_usd for cost in costs if cost is not None)
        comp_sum = sum(cost.completion_cost_usd for cost in costs if cost is not None)
        total_sum = sum(cost.total_cost_usd for cost in costs if cost is not None)
    except (TypeError, OverflowError):
        return None

    if (
        not math.isfinite(prompt_sum)
        or not math.isfinite(comp_sum)
        or not math.isfinite(total_sum)
    ):
        return None
    if prompt_sum < 0.0 or comp_sum < 0.0 or total_sum < 0.0:
        return None

    return MonetaryCost(
        currency=currency,
        prompt_cost_usd=prompt_sum,
        completion_cost_usd=comp_sum,
        total_cost_usd=total_sum,
        calibrated=all(cost.calibrated for cost in costs if cost is not None),
        rate_sources=sorted({
            source for cost in costs if cost is not None for source in cost.rate_sources
        }),
    )


def format_monetary_cost(cost: Any) -> str:
    if cost is None:
        return "uncalibrated"
    try:
        total = getattr(cost, "total_cost_usd", None)
        prompt = getattr(cost, "prompt_cost_usd", None)
        comp = getattr(cost, "completion_cost_usd", None)
        currency = getattr(cost, "currency", None)
        if total is None or prompt is None or comp is None or currency is None:
            return "uncalibrated"
        if (
            not isinstance(total, (int, float))
            or not isinstance(prompt, (int, float))
            or not isinstance(comp, (int, float))
            or isinstance(total, bool)
            or isinstance(prompt, bool)
            or isinstance(comp, bool)
        ):
            return "uncalibrated"
        if not math.isfinite(total) or not math.isfinite(prompt) or not math.isfinite(comp):
            return "uncalibrated"
        if total < 0.0 or prompt < 0.0 or comp < 0.0:
            return "uncalibrated"
        return f"${total:.6f} {currency} (prompt: ${prompt:.6f}, completion: ${comp:.6f})"
    except (AttributeError, TypeError, ValueError):
        return "uncalibrated"
