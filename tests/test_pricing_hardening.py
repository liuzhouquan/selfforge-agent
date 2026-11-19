"""Security, precision, and validation hardening tests for pricing engine (SF-PRICE-02)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime.pricing import (
    ModelRates,
    MonetaryCost,
    estimate_monetary_cost,
    format_monetary_cost,
    sum_monetary_costs,
)


class TestModelRatesAndMonetaryCostHardening:
    """Verifies strict field validation and total invariants."""

    def test_model_rates_rejects_boolean_rates(self) -> None:
        """Boolean values for rates per million must be rejected, not coerced to 1.0."""
        for bad_rate in (True, False):
            with pytest.raises((TypeError, ValueError, ValidationError)):
                ModelRates.model_validate({
                    "prompt_per_million": bad_rate,
                    "completion_per_million": 0.28,
                    "source": "test",
                    "as_of": "2026-11-17",
                })
            with pytest.raises((TypeError, ValueError, ValidationError)):
                ModelRates.model_validate({
                    "prompt_per_million": 0.14,
                    "completion_per_million": bad_rate,
                    "source": "test",
                    "as_of": "2026-11-17",
                })

    def test_monetary_cost_rejects_inconsistent_total(self) -> None:
        """total_cost_usd must be consistent with prompt_cost_usd + completion_cost_usd."""
        with pytest.raises((ValueError, ValidationError)):
            MonetaryCost(
                currency="USD",
                prompt_cost_usd=10.0,
                completion_cost_usd=20.0,
                total_cost_usd=5.0,  # Contradicts prompt + completion (30.0)
            )

        with pytest.raises((ValueError, ValidationError)):
            MonetaryCost(
                currency="USD",
                prompt_cost_usd=1.0,
                completion_cost_usd=2.0,
                total_cost_usd=999.0,  # Contradicts prompt + completion (3.0)
            )

    def test_sum_monetary_costs_rejects_uncalibrated_costs(self) -> None:
        """sum_monetary_costs must withhold total (return None) if any cost is uncalibrated."""
        calibrated_cost = MonetaryCost(
            currency="USD", prompt_cost_usd=1.0, completion_cost_usd=2.0, total_cost_usd=3.0, calibrated=True
        )
        uncalibrated_cost = MonetaryCost(
            currency="USD", prompt_cost_usd=1.0, completion_cost_usd=2.0, total_cost_usd=3.0, calibrated=False
        )
        assert sum_monetary_costs([calibrated_cost, uncalibrated_cost]) is None
        assert sum_monetary_costs([uncalibrated_cost]) is None


class TestEstimateMonetaryCostHardening:
    """Verifies input sanitation, total_tokens consistency, and deep JSON nesting defense."""

    def test_estimate_monetary_cost_rejects_empty_or_whitespace_model_or_provider(
        self, configured_rates: None
    ) -> None:
        """Blank or whitespace-only model or provider must be rejected fail-closed."""
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert estimate_monetary_cost("   ", usage, provider="deepseek") is None
        assert estimate_monetary_cost("deepseek-chat", usage, provider="   ") is None
        assert estimate_monetary_cost("", usage, provider="deepseek") is None
        assert estimate_monetary_cost("deepseek-chat", usage, provider="") is None

    def test_estimate_monetary_cost_rejects_inconsistent_total_tokens(
        self, configured_rates: None
    ) -> None:
        """If total_tokens is explicitly less than prompt + completion, fail-closed."""
        # Duck-typed usage with contradictory total_tokens
        class BadTotalUsage:
            prompt_tokens = 1000
            completion_tokens = 500
            total_tokens = 200  # Contradicts prompt + comp = 1500

        assert estimate_monetary_cost("deepseek-chat", BadTotalUsage(), provider="deepseek") is None  # type: ignore[arg-type]

    def test_estimate_monetary_cost_rejects_deeply_nested_pricing_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deeply nested JSON structures (> 4 levels) in SELFFORGE_PRICING_JSON must be rejected."""
        nested = {"deepseek": {"deepseek-chat": {"rates": {"nested": {"deep": 1}}}}}
        monkeypatch.setenv("SELFFORGE_PRICING_JSON", json.dumps(nested))
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None

    def test_estimate_monetary_cost_rejects_control_characters_in_model(
        self, configured_rates: None
    ) -> None:
        """Models with non-printable control characters (ASCII < 32) must be rejected."""
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert estimate_monetary_cost("deepseek\x07chat", usage, provider="deepseek") is None
        assert estimate_monetary_cost("deepseek\x1bchat", usage, provider="deepseek") is None


class TestFormatMonetaryCostHardening:
    """Verifies uncalibrated flag handling and excessive cost values."""

    def test_format_monetary_cost_returns_uncalibrated_for_uncalibrated_cost(self) -> None:
        """When cost.calibrated is False, format_monetary_cost must return 'uncalibrated'."""
        cost = MonetaryCost(
            currency="USD", prompt_cost_usd=1.0, completion_cost_usd=2.0, total_cost_usd=3.0, calibrated=False
        )
        assert format_monetary_cost(cost) == "uncalibrated"

    def test_format_monetary_cost_rejects_excessive_cost_values(self) -> None:
        """Astronomical or unreasonable cost values (> 1e12) must return 'uncalibrated'."""
        huge_cost = SimpleNamespace(
            prompt_cost_usd=1e13,
            completion_cost_usd=1e13,
            total_cost_usd=2e13,
            currency="USD",
            calibrated=True,
        )
        assert format_monetary_cost(huge_cost) == "uncalibrated"


class TestSumMonetaryCostsHardening:
    """Verifies sequence type checking and bounds."""

    def test_sum_monetary_costs_rejects_non_sequence_or_empty(self) -> None:
        """Non-list/tuple inputs must safely return None."""
        assert sum_monetary_costs("not a list") is None  # type: ignore[arg-type]
        assert sum_monetary_costs(123) is None  # type: ignore[arg-type]
        assert sum_monetary_costs([]) is None

    def test_sum_monetary_costs_rejects_excessive_item_count(self) -> None:
        """Passing more than 100,000 items must fail-closed."""
        dummy = MonetaryCost(currency="USD", prompt_cost_usd=0.0, completion_cost_usd=0.0, total_cost_usd=0.0)
        assert sum_monetary_costs([dummy] * 100_001) is None
