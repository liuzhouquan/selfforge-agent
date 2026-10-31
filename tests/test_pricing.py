"""Tests for calibrated model pricing and monetary cost estimation."""

import pytest

from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime.pricing import (
    MonetaryCost,
    estimate_monetary_cost,
    format_monetary_cost,
)


def test_estimate_monetary_cost_for_known_models() -> None:
    usage = TokenUsage(prompt_tokens=1_000, completion_tokens=500, total_tokens=1_500)
    cost = estimate_monetary_cost("deepseek-chat", usage)
    assert cost is not None
    assert cost.calibrated is True
    assert cost.currency == "USD"
    # prompt: 1000/1M * 0.14 = 0.00014, completion: 500/1M * 0.28 = 0.00014
    assert pytest.approx(cost.prompt_cost_usd, rel=1e-4) == 0.00014
    assert pytest.approx(cost.completion_cost_usd, rel=1e-4) == 0.00014
    assert pytest.approx(cost.total_cost_usd, rel=1e-4) == 0.00028


def test_estimate_monetary_cost_normalizes_model_name() -> None:
    usage = TokenUsage(prompt_tokens=2_000, completion_tokens=1_000, total_tokens=3_000)
    cost1 = estimate_monetary_cost("openai/gpt-4o-mini", usage)
    cost2 = estimate_monetary_cost("gpt-4o-mini", usage)
    assert cost1 is not None and cost2 is not None
    assert cost1.total_cost_usd == cost2.total_cost_usd


def test_estimate_monetary_cost_returns_none_for_unknown_model_or_none_usage() -> None:
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    assert estimate_monetary_cost("unknown-model-xyz", usage) is None
    assert estimate_monetary_cost("deepseek-chat", None) is None


def test_format_monetary_cost() -> None:
    cost = MonetaryCost(
        currency="USD",
        prompt_cost_usd=0.00014,
        completion_cost_usd=0.00028,
        total_cost_usd=0.00042,
        calibrated=True,
    )
    formatted = format_monetary_cost(cost)
    assert "$0.000420 USD" in formatted
    assert format_monetary_cost(None) == "uncalibrated"
