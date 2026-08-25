"""estimate_cost_usd: Anthropic-only pricing. openai_compatible covers
everything from a free local Ollama server to metered hosted APIs, so it
must report 0.0 rather than a fabricated dollar figure -- otherwise
daily_spend_limit_usd enforcement (SpendTracker.check_before_call) halts a
genuinely free local run over cost that was never spent."""
from hanno.excavator.spend_tracker import estimate_cost_usd


def test_anthropic_provider_uses_real_pricing():
    cost = estimate_cost_usd(1_000_000, 1_000_000, provider="anthropic")
    assert cost == 3.0 + 15.0


def test_anthropic_is_the_default_provider():
    cost = estimate_cost_usd(1_000_000, 0)
    assert cost == 3.0


def test_openai_compatible_provider_reports_zero_cost():
    cost = estimate_cost_usd(1_000_000, 1_000_000, provider="openai_compatible")
    assert cost == 0.0
