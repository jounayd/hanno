"""Hard daily spend limit enforcement (Rule 4 — spend limits are hard limits)."""
from __future__ import annotations

from hanno.ledger.repository import Ledger

# Approximate per-million-token pricing (USD). Keep this updated — it is an
# estimate, not billing truth. Used only to enforce the configured daily cap.
PRICE_PER_MILLION_INPUT_TOKENS = 3.0
PRICE_PER_MILLION_OUTPUT_TOKENS = 15.0


class SpendLimitExceeded(Exception):
    pass


def estimate_cost_usd(input_tokens: int, output_tokens: int, provider: str = "anthropic") -> float:
    """Anthropic-only pricing table. For provider="openai_compatible" this
    returns 0.0 rather than guess -- that provider covers everything from a
    free local Ollama server to metered hosted APIs, and Hanno has no way to
    know which. Reporting a real dollar figure it can't back up is worse than
    reporting none: `daily_spend_limit_usd` enforcement (see SpendTracker
    below) would otherwise halt a genuinely free local run over a fictitious
    cost that was never actually spent."""
    if provider != "anthropic":
        return 0.0
    return (
        input_tokens / 1_000_000 * PRICE_PER_MILLION_INPUT_TOKENS
        + output_tokens / 1_000_000 * PRICE_PER_MILLION_OUTPUT_TOKENS
    )


class SpendTracker:
    """Tracks running cost for the in-progress cycle and checks it against
    the configured daily cap (today's spend already recorded in the ledger
    plus what has been spent so far this cycle)."""

    def __init__(self, ledger: Ledger, daily_limit_usd: float):
        self._ledger = ledger
        self._daily_limit_usd = daily_limit_usd
        self._cycle_spend_usd = 0.0

    @property
    def cycle_spend_usd(self) -> float:
        return self._cycle_spend_usd

    def check_before_call(self) -> None:
        already_spent_today = self._ledger.get_today_spend_usd()
        if already_spent_today + self._cycle_spend_usd >= self._daily_limit_usd:
            raise SpendLimitExceeded(
                f"daily spend limit of ${self._daily_limit_usd:.2f} reached "
                f"(today so far: ${already_spent_today + self._cycle_spend_usd:.2f})"
            )

    def record(self, cost_usd: float) -> None:
        self._cycle_spend_usd += cost_usd
