"""Security, type safety, and currency consistency tests for model pricing (SF-PRICE-01)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Ensure local worktree src is loaded first
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from selfforge_agent.providers import TokenUsage
from selfforge_agent.runtime.pricing import (
    MAX_PRICING_JSON_BYTES,
    MAX_TOKENS_PER_REQUEST,
    MonetaryCost,
    estimate_monetary_cost,
    format_monetary_cost,
    sum_monetary_costs,
)


def test_pricing_rejects_boolean_and_non_int_token_usage(configured_rates: None) -> None:
    # Boolean prompt_tokens or completion_tokens must be rejected (fail-closed)
    class DuckUsage:
        def __init__(self, prompt: object, completion: object) -> None:
            self.prompt_tokens = prompt
            self.completion_tokens = completion

    for bad_prompt, bad_completion in (
        (True, 500),
        (500, False),
        (True, True),
        (1.5, 500),  # float
        ("1000", 500),  # string
        (-1, 500),  # negative
        (500, -10),  # negative
    ):
        usage = DuckUsage(bad_prompt, bad_completion)
        assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None  # type: ignore[arg-type]

    # Negative tokens in TokenUsage
    neg_usage = TokenUsage(prompt_tokens=-1, completion_tokens=500, total_tokens=499)
    assert estimate_monetary_cost("deepseek-chat", neg_usage, provider="deepseek") is None


def test_pricing_rejects_mixed_currency_in_sum_monetary_costs() -> None:
    cost_usd = MonetaryCost(
        currency="USD", prompt_cost_usd=1.0, completion_cost_usd=1.0, total_cost_usd=2.0
    )
    cost_eur = MonetaryCost(
        currency="EUR", prompt_cost_usd=1.0, completion_cost_usd=1.0, total_cost_usd=2.0
    )
    # Mixed currencies cannot be blindly summed as USD
    assert sum_monetary_costs([cost_usd, cost_eur]) is None


def test_pricing_enforces_iso_currency_code() -> None:
    for bad_curr in ("", "US", "USDD", "usd", "U$D", "123", "US\x00"):
        with pytest.raises((ValueError, ValidationError)):
            MonetaryCost(
                currency=bad_curr,
                prompt_cost_usd=1.0,
                completion_cost_usd=1.0,
                total_cost_usd=2.0,
            )


def test_pricing_rejects_malformed_and_oversized_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)

    # 1. Non-dict JSON root
    for non_dict in ("[]", "123", '"string"', "true", "null"):
        monkeypatch.setenv("SELFFORGE_PRICING_JSON", non_dict)
        assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None

    # 2. Oversized pricing JSON (> 16 KB)
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", " " * (MAX_PRICING_JSON_BYTES + 1))
    assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None

    # 3. Non-dict provider or model mappings
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", json.dumps({"deepseek": "not-a-dict"}))
    assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None

    monkeypatch.setenv(
        "SELFFORGE_PRICING_JSON",
        json.dumps({"deepseek": {"deepseek-chat": "not-a-card"}}),
    )
    assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None

    # 4. Control characters or null bytes in provider / model arguments
    card = {
        "prompt_per_million": 0.14,
        "completion_per_million": 0.28,
        "source": "test",
        "as_of": "2026-10-31",
    }
    raw = json.dumps({"deepseek": {"deepseek-chat": card}})
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", raw)
    assert estimate_monetary_cost("deepseek-chat\x00", usage, provider="deepseek") is None
    assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek\x00") is None
    assert estimate_monetary_cost("deepseek-chat\r\n", usage, provider="deepseek") is None
    assert estimate_monetary_cost("deepseek-chat\t", usage, provider="deepseek") is None


def test_format_monetary_cost_handles_invalid_cost() -> None:
    assert format_monetary_cost(None) == "uncalibrated"
    # Negative total or NaN
    fake_cost = type(
        "Fake",
        (),
        {
            "total_cost_usd": float("nan"),
            "prompt_cost_usd": 1.0,
            "completion_cost_usd": 1.0,
            "currency": "USD",
        },
    )()
    assert format_monetary_cost(fake_cost) == "uncalibrated"  # type: ignore[arg-type]

    # Negative amount
    neg_cost = type(
        "FakeNeg",
        (),
        {
            "total_cost_usd": -1.0,
            "prompt_cost_usd": 0.0,
            "completion_cost_usd": 0.0,
            "currency": "USD",
        },
    )()
    assert format_monetary_cost(neg_cost) == "uncalibrated"  # type: ignore[arg-type]


def test_pricing_rejects_extreme_and_overflow_token_counts(configured_rates: None) -> None:
    # Extreme token count beyond reasonable bounds
    usage = TokenUsage(
        prompt_tokens=MAX_TOKENS_PER_REQUEST + 1,
        completion_tokens=500,
        total_tokens=MAX_TOKENS_PER_REQUEST + 501,
    )
    assert estimate_monetary_cost("deepseek-chat", usage, provider="deepseek") is None


@pytest.mark.parametrize("currency", ["bad\nlabel", "usd", ["USD"], 123])
def test_formatter_rejects_invalid_currency_on_unvalidated_objects(currency) -> None:
    from types import SimpleNamespace

    value = SimpleNamespace(prompt_cost_usd=1.0, completion_cost_usd=2.0,
                            total_cost_usd=3.0, currency=currency)
    assert format_monetary_cost(value) == "uncalibrated"


def test_non_usd_rate_card_cannot_establish_usd_budget_evidence(tmp_path, monkeypatch) -> None:
    from selfforge_agent.runtime.session import SessionStore

    card = {"prompt_per_million": 1, "completion_per_million": 2,
            "source": "fixture", "as_of": "2026-11-16", "currency": "EUR"}
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", json.dumps({"fixture": {"model": card}}))
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert estimate_monetary_cost("model", usage, provider="fixture") is None
    session = SessionStore(tmp_path)
    session.trace("model_call", provider="fixture", model="model", usage=usage.model_dump())
    assert session.token_metrics()["cost"] is None


def test_non_usd_explicit_same_currency_sum_and_format_remain_available() -> None:
    value = MonetaryCost(currency="EUR", prompt_cost_usd=1, completion_cost_usd=2, total_cost_usd=3)
    result = sum_monetary_costs([value, value])
    assert result is not None and result.currency == "EUR" and result.total_cost_usd == 6
    assert format_monetary_cost(result).startswith("EUR 6.000000")
    assert "$" not in format_monetary_cost(result)


def test_rate_card_limit_counts_utf8_bytes(monkeypatch) -> None:
    card = {"prompt_per_million": 1, "completion_per_million": 2,
            "source": "fixture", "as_of": "2026-11-16"}
    raw = json.dumps({"p": {"m": card}, "padding": "中" * 6000}, ensure_ascii=False)
    assert len(raw) < MAX_PRICING_JSON_BYTES < len(raw.encode("utf-8"))
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", raw)
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert estimate_monetary_cost("m", usage, provider="p") is None


def test_formatter_handles_integer_too_large_for_float_conversion() -> None:
    from types import SimpleNamespace

    value = SimpleNamespace(prompt_cost_usd=10**1000, completion_cost_usd=1,
                            total_cost_usd=10**1000, currency="USD")
    assert format_monetary_cost(value) == "uncalibrated"
