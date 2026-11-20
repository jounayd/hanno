"""path_score: a composite quality signal per candidate, used to feed the
arm selector (hanno/excavator/arm_selector.py). Not a substitute for real
verification (Sieve/Lean, later steps) — a cheap proxy so the Excavator can
learn which (model, temperature, prompt_variant, reasoning_effort) 'arms'
are actually producing good candidates, versus which are dead weight.
"""

from __future__ import annotations

NEAR_DUPLICATE_MULTIPLIER = 0.3
DEAD_END_MULTIPLIER = 0.5
NUMERIC_CHECK_PASS_MULTIPLIER = 1.2
MISSING_WITNESS_MULTIPLIER = 0.6  # construction claims that skip their required witness


def compute_path_score(
    self_rated_novelty: float,
    self_rated_rigor: float,
    duplicate_status: str | None,
    exploration_signal: str | None,
    numeric_check_result: bool | None = None,
    claim_type: str | None = None,
) -> float:
    if duplicate_status == "EXACT_DUPLICATE":
        return 0.0
    # A self-supplied numeric witness that mechanically fails is decisive —
    # it directly falsifies the claim as stated, regardless of how confident
    # the model's own self_rated_rigor was. This applies to every claim_type:
    # even where a witness can't verify the claim, its failure is damning.
    if numeric_check_result is False:
        return 0.0

    score = 0.5 * self_rated_novelty + 0.5 * self_rated_rigor

    if duplicate_status == "NEAR_DUPLICATE":
        score *= NEAR_DUPLICATE_MULTIPLIER
    if exploration_signal == "dead_end":
        score *= DEAD_END_MULTIPLIER

    # Only a "construction" claim — one that exhibits explicit checkable
    # objects — can earn the verification bonus, and only such a claim is
    # penalized for skipping verification. Asymptotic claims can't be
    # verified by one example, so a "passed" witness there is hollow by
    # definition and earns nothing (observed in practice: witnesses that
    # only re-check the model's own invented numbers for self-consistency).
    if claim_type == "construction":
        if numeric_check_result is True:
            score *= NUMERIC_CHECK_PASS_MULTIPLIER
        else:  # required witness missing or unevaluable
            score *= MISSING_WITNESS_MULTIPLIER

    return max(0.0, min(1.0, score))
