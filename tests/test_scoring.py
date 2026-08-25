"""compute_path_score: composite candidate quality signal feeding the arm
selector. Duplicates and dead ends must be penalized regardless of how
confident the model's own self-ratings were."""
import pytest

from hanno.excavator.scoring import compute_path_score


def test_novel_continue_is_average_of_self_ratings():
    score = compute_path_score(
        self_rated_novelty=0.8, self_rated_rigor=0.4,
        duplicate_status="NOVEL", exploration_signal="continue",
    )
    assert score == pytest.approx(0.6)


def test_exact_duplicate_is_zero_regardless_of_confidence():
    score = compute_path_score(
        self_rated_novelty=1.0, self_rated_rigor=1.0,
        duplicate_status="EXACT_DUPLICATE", exploration_signal="continue",
    )
    assert score == 0.0


def test_near_duplicate_is_heavily_discounted():
    score = compute_path_score(
        self_rated_novelty=1.0, self_rated_rigor=1.0,
        duplicate_status="NEAR_DUPLICATE", exploration_signal="continue",
    )
    assert score == pytest.approx(0.3)


def test_dead_end_is_halved():
    score = compute_path_score(
        self_rated_novelty=0.8, self_rated_rigor=0.8,
        duplicate_status="NOVEL", exploration_signal="dead_end",
    )
    assert score == pytest.approx(0.4)


def test_near_duplicate_and_dead_end_compound():
    score = compute_path_score(
        self_rated_novelty=1.0, self_rated_rigor=1.0,
        duplicate_status="NEAR_DUPLICATE", exploration_signal="dead_end",
    )
    assert score == pytest.approx(0.15)


def test_branch_point_is_not_penalized_on_its_own():
    score = compute_path_score(
        self_rated_novelty=0.6, self_rated_rigor=0.6,
        duplicate_status="NOVEL", exploration_signal="branch_point",
    )
    assert score == pytest.approx(0.6)


def test_failing_numeric_check_overrides_everything_to_zero():
    for claim_type in ("construction", "asymptotic", "structural", None):
        score = compute_path_score(
            self_rated_novelty=0.95, self_rated_rigor=0.95,
            duplicate_status="NOVEL", exploration_signal="continue",
            numeric_check_result=False, claim_type=claim_type,
        )
        assert score == 0.0


def test_passing_numeric_check_on_construction_gives_a_capped_bonus():
    score = compute_path_score(
        self_rated_novelty=0.9, self_rated_rigor=0.9,
        duplicate_status="NOVEL", exploration_signal="continue",
        numeric_check_result=True, claim_type="construction",
    )
    assert score == 1.0  # 0.9 * 1.2 = 1.08, capped at 1.0


def test_construction_without_witness_is_penalized():
    score = compute_path_score(
        self_rated_novelty=0.5, self_rated_rigor=0.5,
        duplicate_status="NOVEL", exploration_signal="continue",
        numeric_check_result=None, claim_type="construction",
    )
    assert score == pytest.approx(0.5 * 0.6)


def test_hollow_pass_on_asymptotic_earns_no_bonus():
    # A "passed" witness on an asymptotic claim can only ever be
    # self-consistency of invented numbers -- it must score the same as no
    # witness at all.
    with_pass = compute_path_score(
        0.5, 0.5, "NOVEL", "continue", numeric_check_result=True, claim_type="asymptotic",
    )
    without = compute_path_score(
        0.5, 0.5, "NOVEL", "continue", numeric_check_result=None, claim_type="asymptotic",
    )
    assert with_pass == without == pytest.approx(0.5)


def test_structural_claim_without_witness_is_neutral():
    score = compute_path_score(
        0.5, 0.5, "NOVEL", "continue", numeric_check_result=None, claim_type="structural",
    )
    assert score == pytest.approx(0.5)
