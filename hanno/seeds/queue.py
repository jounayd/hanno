"""Seed priority queue ordering.

Priority = fertility (higher = higher priority, unknown for new seeds gets
a recency boost instead) + recency boost for newly queued seeds + a domain
balance adjustment so no single domain dominates the queue + a cross-domain
bonus (General-spec.txt:353-356: cross-domain seeds get a priority bonus
because cross-domain exploration is where the most surprising discoveries
are found). Cartographer signal (Step 11) is not implemented yet —
placeholder weight of 0.
"""
from __future__ import annotations

from datetime import datetime, timezone

RECENCY_BOOST = 0.3          # applied to seeds with no fertility data yet
RECENCY_WINDOW_HOURS = 72    # boost decays to 0 after this many hours
DOMAIN_BALANCE_WEIGHT = 0.2
CROSS_DOMAIN_BONUS = 0.15    # General-spec.txt:354-356


def _recency_score(submitted_at: str) -> float:
    try:
        submitted = datetime.fromisoformat(submitted_at)
    except ValueError:
        return 0.0
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - submitted).total_seconds() / 3600
    if age_hours >= RECENCY_WINDOW_HOURS:
        return 0.0
    return RECENCY_BOOST * (1 - age_hours / RECENCY_WINDOW_HOURS)


def compute_priority(seed: dict, active_domain_counts: dict[str, int]) -> float:
    """seed: dict from Ledger.list_seeds() (includes status, cycles_run,
    candidates_produced, discoveries_attributed). active_domain_counts:
    domain -> count of active seeds, used to de-prioritize an
    over-represented domain."""
    cycles_run = seed.get("cycles_run", 0)
    discoveries_attributed = seed.get("discoveries_attributed", 0)

    # General-spec.txt:328: "Fertility score (computed after use:
    # discoveries produced / cycles run)" — NOT candidates produced.
    if cycles_run > 0:
        fertility = discoveries_attributed / cycles_run
    else:
        fertility = 0.0

    recency = 0.0 if cycles_run > 0 else _recency_score(seed["submitted_at"])

    total_active = sum(active_domain_counts.values()) or 1
    domain_share = active_domain_counts.get(seed["domain"], 0) / total_active
    domain_balance_penalty = DOMAIN_BALANCE_WEIGHT * domain_share

    cross_domain_bonus = CROSS_DOMAIN_BONUS if seed["domain"] == "cross-domain" else 0.0

    return fertility + recency + cross_domain_bonus - domain_balance_penalty


def order_queue(seeds: list[dict]) -> list[dict]:
    """seeds: list of seed dicts with status already resolved. Only
    'queued' or 'active' seeds are ordered; exhausted/retired sort last."""
    active_domain_counts: dict[str, int] = {}
    for s in seeds:
        if s["status"] in ("queued", "active"):
            active_domain_counts[s["domain"]] = active_domain_counts.get(s["domain"], 0) + 1

    def sort_key(seed: dict):
        eligible = seed["status"] in ("queued", "active")
        priority = compute_priority(seed, active_domain_counts) if eligible else float("-inf")
        return (not eligible, -priority)

    return sorted(seeds, key=sort_key)
