"""Adaptive selection of generation parameters ('arms'): which
(model, temperature, prompt_variant, reasoning_effort) combination to use
for the next candidate. Replaces pure round-robin with UCB1 — untried arms
are always tried first (cold start), then selection shifts toward whichever
arms have historically produced the highest path_score, while
`exploration_constant` keeps a standing budget for trying less-proven arms
so the search never fully stops sailing new headings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

REASONING_EFFORT_STANDARD = "standard"
REASONING_EFFORT_DEEP = "deep"

# Extended thinking requires the API's fixed sampling temperature — arms in
# "deep" mode don't vary temperature the way "standard" arms do.
DEEP_REASONING_TEMPERATURE = 1.0


@dataclass(frozen=True)
class Arm:
    model: str
    temperature: float
    prompt_variant: str
    reasoning_effort: str


def _diagonal_order(temperatures: list[float], variants: list[str]) -> list[tuple[float, str]]:
    """Order every (temperature, variant) cell by antidiagonal (i+j), so that
    a small prefix of the list already varies both temperature AND variant —
    not just one axis. Cold-start selection (see select_plan) walks arms in
    this list order, so this is what guarantees a cheap, small --candidates
    run still samples the exploratory temperatures instead of exhausting
    every variant at the lowest temperature first."""
    cells = [(i, j) for i in range(len(temperatures)) for j in range(len(variants))]
    cells.sort(key=lambda ij: (ij[0] + ij[1], ij[0]))
    return [(temperatures[i], variants[j]) for i, j in cells]


def build_arm_space(
    model: str, temperatures: list[float], variants: list[str], reasoning_efforts: list[str],
) -> list[Arm]:
    arms: list[Arm] = []
    if REASONING_EFFORT_STANDARD in reasoning_efforts:
        for temperature, variant in _diagonal_order(temperatures, variants):
            arms.append(Arm(model, temperature, variant, REASONING_EFFORT_STANDARD))
    if REASONING_EFFORT_DEEP in reasoning_efforts:
        for variant in variants:
            arms.append(Arm(model, DEEP_REASONING_TEMPERATURE, variant, REASONING_EFFORT_DEEP))
    return arms


def _arm_key(arm: Arm) -> tuple:
    return (arm.model, arm.temperature, arm.prompt_variant, arm.reasoning_effort)


def select_plan(
    n_candidates: int, arms: list[Arm], arm_stats: list[dict], exploration_constant: float = 1.4,
) -> list[Arm]:
    """arm_stats: rows from Ledger.get_arm_stats(domain), each with keys
    model, temperature, prompt_variant, reasoning_effort, pulls, avg_score."""
    if not arms:
        raise ValueError("no arms to select from")

    stats_by_key = {
        (s["model"], s["temperature"], s["prompt_variant"], s["reasoning_effort"]): s
        for s in arm_stats
    }
    pulls = {}
    avg_score = {}
    for arm in arms:
        s = stats_by_key.get(_arm_key(arm))
        pulls[arm] = s["pulls"] if s else 0
        avg_score[arm] = s["avg_score"] if s else 0.0

    plan: list[Arm] = []
    for _ in range(n_candidates):
        untried = [a for a in arms if pulls[a] == 0]
        if untried:
            chosen = untried[0]
        else:
            total_pulls = sum(pulls.values())
            chosen = max(
                arms,
                key=lambda a: avg_score[a]
                + exploration_constant * math.sqrt(math.log(total_pulls + 1) / pulls[a]),
            )
        plan.append(chosen)
        pulls[chosen] += 1

    return plan
