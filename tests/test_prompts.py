"""build_prompt: the already_explored injection is what lets later mining
cycles diverge from what's already been produced, instead of every call
independently reconverging on the same well-known angle."""

from hanno.excavator.prompts import ALL_VARIANTS, STEERING_HINTS, build_prompt


def test_no_already_explored_by_default():
    prompt = build_prompt("a seed about number theory", "direct_conjecture")
    assert "already been explored" not in prompt
    assert "a seed about number theory" in prompt


def test_already_explored_is_injected_and_listed():
    prompt = build_prompt(
        "a seed about number theory",
        "direct_conjecture",
        already_explored=["claim about residue classes mod 840", "claim about Egyptian fractions"],
    )
    assert "already been explored" in prompt
    assert "claim about residue classes mod 840" in prompt
    assert "claim about Egyptian fractions" in prompt


def test_empty_already_explored_list_is_same_as_none():
    with_empty = build_prompt("seed text", "direct_conjecture", already_explored=[])
    with_none = build_prompt("seed text", "direct_conjecture", already_explored=None)
    assert with_empty == with_none


def test_no_steering_hint_by_default():
    prompt = build_prompt("a seed about number theory", "direct_conjecture")
    assert "For this attempt specifically" not in prompt


def test_steering_hint_is_injected():
    prompt = build_prompt(
        "a seed about number theory",
        "direct_conjecture",
        steering_hint="Focus on a probabilistic argument.",
    )
    assert "For this attempt specifically: Focus on a probabilistic argument." in prompt


def test_steering_hints_are_all_distinct():
    assert len(STEERING_HINTS) == len(set(STEERING_HINTS))
    assert len(STEERING_HINTS) >= 8


def test_unknown_variant_raises():
    import pytest

    with pytest.raises(ValueError):
        build_prompt("seed text", "not_a_real_variant")


def test_all_variants_produce_valid_prompts():
    for variant in ALL_VARIANTS:
        prompt = build_prompt("a seed about topology and manifolds", variant)
        assert "numeric_check" in prompt
        assert "self_rated_novelty" in prompt
