"""Estimate spend only from explicitly configured provider/model rate cards."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from selfforge_agent.providers import TokenUsage

MAX_TOKENS_PER_REQUEST = 1_000_000_000
MAX_PRICING_JSON_BYTES = 16_384
MAX_COST_ITEMS_TO_SUM = 100_000
MAX_REASONABLE_COST = 1_000_000_000.0
MAX_FORMAT_COST = 1_000_000_000_000.0

_PRICING_CACHE: dict[str, dict[str, dict[str, ModelRates]]] = {}


class ModelRates(BaseModel):
    prompt_per_million: float = Field(ge=0.0, le=1_000_000.0, allow_inf_nan=False)
    completion_per_million: float = Field(ge=0.0, le=1_000_000.0, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=500)
    as_of: date
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @field_validator("prompt_per_million", "completion_per_million", mode="before")
    @classmethod
    def _validate_numeric_rates(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise TypeError("Rate per million cannot be boolean")
        if not isinstance(value, (int, float)):
            raise TypeError("Rate per million must be numeric")
        return value


class MonetaryCost(BaseModel):
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    prompt_cost_usd: float = Field(ge=0.0, le=MAX_REASONABLE_COST, allow_inf_nan=False)
    completion_cost_usd: float = Field(ge=0.0, le=MAX_REASONABLE_COST, allow_inf_nan=False)
    total_cost_usd: float = Field(ge=0.0, le=MAX_REASONABLE_COST * 2, allow_inf_nan=False)
    calibrated: bool = True
    rate_sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_total_consistency(self) -> MonetaryCost:
        expected = self.prompt_cost_usd + self.completion_cost_usd
        tolerance = 1e-4 * max(1.0, self.total_cost_usd)
        if abs(self.total_cost_usd - expected) > tolerance:
            raise ValueError(
                f"total_cost_usd ({self.total_cost_usd}) is inconsistent with prompt + completion ({expected})"
            )
        return self


def _check_json_depth(obj: Any, depth: int = 1) -> bool:
    if depth > 4:
        return False
    if isinstance(obj, dict):
        return all(_check_json_depth(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(_check_json_depth(v, depth + 1) for v in obj)
    return True


def _get_pricing_table(raw_json: str) -> dict[str, dict[str, ModelRates]] | None:
    if raw_json in _PRICING_CACHE:
        return _PRICING_CACHE[raw_json]

    if (
        not raw_json
        or len(raw_json) > MAX_PRICING_JSON_BYTES
        or "\x00" in raw_json
        or len(raw_json.encode("utf-8", errors="replace")) > MAX_PRICING_JSON_BYTES
    ):
        return None

    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict) or not _check_json_depth(data):
            return None

        table: dict[str, dict[str, ModelRates]] = {}
        for prov, prov_data in data.items():
            if not isinstance(prov, str) or not isinstance(prov_data, dict):
                continue
            table[prov] = {}
            for mdl, card_data in prov_data.items():
                if not isinstance(mdl, str) or not isinstance(card_data, dict):
                    continue
                table[prov][mdl] = ModelRates.model_validate(card_data)

        if len(_PRICING_CACHE) >= 32:
            _PRICING_CACHE.clear()
        _PRICING_CACHE[raw_json] = table
        return table
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return None


def estimate_monetary_cost(
    model: str, usage: TokenUsage | None, *, provider: str | None = None
) -> MonetaryCost | None:
    """A model name alone cannot identify endpoint pricing, discounts, or caches."""
    if usage is None or not provider or not isinstance(provider, str) or not isinstance(model, str):
        return None
    if not provider.strip() or not model.strip():
        return None
    if len(provider) > 256 or len(model) > 256:
        return None
    if any(ord(c) < 32 for c in provider + model):
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

    total_toks = getattr(usage, "total_tokens", None)
    if total_toks is not None:
        if type(total_toks) is not int or total_toks < 0:
            return None
        if total_toks > MAX_TOKENS_PER_REQUEST * 2:
            return None
        if total_toks < prompt_toks + comp_toks:
            return None

    configured = os.environ.get("SELFFORGE_PRICING_JSON", "")
    pricing_table = _get_pricing_table(configured)
    if pricing_table is None:
        return None

    provider_data = pricing_table.get(provider)
    if not provider_data:
        return None
    rates = provider_data.get(model)
    if not rates:
        return None

    # All production consumers use USD fields/budgets; no implicit FX conversion.
    if rates.currency != "USD":
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
    if total > MAX_REASONABLE_COST:
        return None

    try:
        return MonetaryCost(
            currency=rates.currency,
            prompt_cost_usd=prompt,
            completion_cost_usd=completion,
            total_cost_usd=total,
            calibrated=True,
            rate_sources=[f"{rates.source} ({rates.as_of.isoformat()})"],
        )
    except (ValueError, TypeError):
        return None


def sum_monetary_costs(costs: list[MonetaryCost | None]) -> MonetaryCost | None:
    """Withhold the total if any call lacks calibrated usage or pricing, or currencies mismatch."""
    if not isinstance(costs, (list, tuple)):
        return None
    if not costs or len(costs) > MAX_COST_ITEMS_TO_SUM:
        return None
    if any(cost is None or not isinstance(cost, MonetaryCost) for cost in costs):
        return None
    if any(not cost.calibrated for cost in costs):  # type: ignore[union-attr]
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
    if total_sum > MAX_REASONABLE_COST * 2:
        return None

    try:
        return MonetaryCost(
            currency=currency,
            prompt_cost_usd=prompt_sum,
            completion_cost_usd=comp_sum,
            total_cost_usd=total_sum,
            calibrated=True,
            rate_sources=sorted({
                source for cost in costs if cost is not None for source in cost.rate_sources
            }),
        )
    except (ValueError, TypeError):
        return None


def format_monetary_cost(cost: Any) -> str:
    if cost is None:
        return "uncalibrated"
    try:
        if getattr(cost, "calibrated", True) is False:
            return "uncalibrated"
        total = getattr(cost, "total_cost_usd", None)
        prompt = getattr(cost, "prompt_cost_usd", None)
        comp = getattr(cost, "completion_cost_usd", None)
        currency = getattr(cost, "currency", None)
        if (total is None or prompt is None or comp is None or not isinstance(currency, str)
                or not re.fullmatch(r"[A-Z]{3}", currency)):
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
        if total > MAX_FORMAT_COST or prompt > MAX_FORMAT_COST or comp > MAX_FORMAT_COST:
            return "uncalibrated"
        if currency == "USD":
            return f"${total:.6f} {currency} (prompt: ${prompt:.6f}, completion: ${comp:.6f})"
        return f"{currency} {total:.6f} (prompt: {prompt:.6f}, completion: {comp:.6f})"
    except (AttributeError, TypeError, ValueError, OverflowError):
        return "uncalibrated"
