"""Seed priority queue: fertility formula and cross-domain bonus.

Per General-spec.txt:328 fertility is discoveries_attributed / cycles_run
(NOT candidates_produced), and per General-spec.txt:353-356 cross-domain
seeds receive an explicit priority bonus.
"""
from hanno.seeds.queue import CROSS_DOMAIN_BONUS, compute_priority


def _seed(domain="mathematics", cycles_run=0, candidates_produced=0,
          discoveries_attributed=0, submitted_at="2020-01-01T00:00:00+00:00"):
    return {
        "domain": domain,
        "cycles_run": cycles_run,
        "candidates_produced": candidates_produced,
        "discoveries_attributed": discoveries_attributed,
        "submitted_at": submitted_at,
    }


def test_fertility_uses_cycles_run_not_candidates_produced():
    # 1 discovery from 2 cycles but 100 candidates -> fertility must be 0.5, not 0.01.
    seed = _seed(cycles_run=2, candidates_produced=100, discoveries_attributed=1)
    priority = compute_priority(seed, active_domain_counts={"mathematics": 1})
    expected_fertility = 1 / 2
    domain_penalty = 0.2 * 1.0  # sole seed in its active domain
    assert priority == expected_fertility - domain_penalty


def test_fertility_formula_matches_spec_exactly():
    seed = _seed(cycles_run=4, candidates_produced=200, discoveries_attributed=2)
    priority = compute_priority(seed, active_domain_counts={"mathematics": 1})
    expected_fertility = 2 / 4  # discoveries / cycles_run
    domain_penalty = 0.2 * 1.0  # this seed is 100% of the single active domain
    assert priority == expected_fertility - domain_penalty


def test_cross_domain_seed_gets_bonus():
    math_seed = _seed(domain="mathematics", cycles_run=1, discoveries_attributed=0)
    cross_seed = _seed(domain="cross-domain", cycles_run=1, discoveries_attributed=0)
    counts = {"mathematics": 1, "cross-domain": 1}
    math_priority = compute_priority(math_seed, counts)
    cross_priority = compute_priority(cross_seed, counts)
    assert cross_priority - math_priority == CROSS_DOMAIN_BONUS


def test_new_seed_with_zero_cycles_gets_recency_not_fertility():
    seed = _seed(cycles_run=0, discoveries_attributed=0)
    from datetime import datetime, timezone
    seed["submitted_at"] = datetime.now(timezone.utc).isoformat()
    priority = compute_priority(seed, active_domain_counts={"mathematics": 1})
    assert priority > 0  # recency boost applies, not zero fertility
