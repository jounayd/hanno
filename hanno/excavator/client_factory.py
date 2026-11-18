"""The one place that maps `config.excavator.provider` to a concrete
ModelClient. Everywhere else in Hanno (ExcavatorPipeline, the CLI commands,
hanno ui's server) calls this instead of constructing a provider class
directly -- replaces what used to be `AnthropicClient(model=..., max_tokens=...,
api_key=...)` duplicated slightly differently in five separate files.

Stays pure: takes an already-resolved api_key rather than reaching into
hanno.ui.secrets itself, so callers control where the key comes from (and
this stays trivially testable without touching the filesystem).
"""

from __future__ import annotations

from hanno.config import HannoConfig
from hanno.excavator.client import AnthropicClient, ModelClient
from hanno.excavator.openai_client import OpenAICompatibleClient
from hanno.excavator.spend_tracker import RequestBudget
from hanno.ledger.repository import Ledger


def _build_client(
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    max_tokens: int,
    timeout_seconds: float,
    provider_field_name: str,
    base_url_field_name: str,
    budget: RequestBudget | None = None,
) -> ModelClient:
    if provider == "anthropic":
        return AnthropicClient(
            model=model,
            max_tokens=max_tokens,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            budget=budget,
        )

    if provider == "openai_compatible":
        if not base_url:
            raise ValueError(
                f"{provider_field_name} is 'openai_compatible' but {base_url_field_name} is not set "
                '(e.g. "http://localhost:11434/v1" for a local Ollama server)'
            )
        return OpenAICompatibleClient(
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(
        f"unknown {provider_field_name} {provider!r} -- expected 'anthropic' or 'openai_compatible'"
    )


def build_client(
    config: HannoConfig,
    api_key: str | None,
    max_tokens: int | None = None,
    ledger: Ledger | None = None,
) -> ModelClient:
    cfg = config.excavator
    resolved_max_tokens = max_tokens if max_tokens is not None else cfg.max_tokens_per_candidate
    return _build_client(
        cfg.provider,
        cfg.model,
        cfg.base_url,
        api_key,
        resolved_max_tokens,
        cfg.request_timeout_seconds,
        "excavator.provider",
        "excavator.base_url",
        RequestBudget(ledger, cfg.daily_spend_limit_usd) if ledger is not None else None,
    )


def build_prover_client(
    config: HannoConfig,
    api_key: str | None,
    max_tokens: int | None = None,
    ledger: Ledger | None = None,
) -> ModelClient | None:
    """A separate, optional client for the Prover role only (Migration Plan
    Phase 6's "dedicated/stronger prover model" idea) -- e.g. a cheap/fast
    model handles formalization while a stronger model handles the actually
    hard proof search. Returns None when `excavator.prover_model` isn't set,
    so every existing caller keeps reusing the single main client (today's
    behavior, unchanged) unless a prover model is explicitly configured."""
    cfg = config.excavator
    if not cfg.prover_model:
        return None
    resolved_max_tokens = max_tokens if max_tokens is not None else cfg.max_tokens_per_candidate
    return _build_client(
        cfg.prover_provider or cfg.provider,
        cfg.prover_model,
        cfg.prover_base_url or cfg.base_url,
        api_key,
        resolved_max_tokens,
        cfg.request_timeout_seconds,
        "excavator.prover_provider",
        "excavator.prover_base_url",
        RequestBudget(ledger, cfg.daily_spend_limit_usd) if ledger is not None else None,
    )
