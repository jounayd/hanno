"""ArmSelector: UCB1 selection over (model, temperature, prompt_variant,
reasoning_effort) 'arms'. Untried arms must be exhausted before the
selector starts exploiting historical averages, and once given clear
performance history it should visibly favor the better-scoring arm."""
from hanno.excavator.arm_selector import Arm, build_arm_space, select_plan

MODEL = "claude-test-model"


def test_build_arm_space_deep_arms_fix_temperature():
    arms = build_arm_space(MODEL, [0.3, 0.7], ["v1", "v2"], ["standard", "deep"])
    standard_arms = [a for a in arms if a.reasoning_effort == "standard"]
    deep_arms = [a for a in arms if a.reasoning_effort == "deep"]

    assert len(standard_arms) == 4  # 2 temps x 2 variants
    assert len(deep_arms) == 2  # 1 fixed temperature x 2 variants
    assert all(a.temperature == 1.0 for a in deep_arms)


def test_build_arm_space_respects_reasoning_efforts_filter():
    arms = build_arm_space(MODEL, [0.3, 0.7], ["v1"], ["standard"])
    assert all(a.reasoning_effort == "standard" for a in arms)
    assert len(arms) == 2


def test_cold_start_covers_every_arm_before_repeating():
    arms = build_arm_space(MODEL, [0.3, 0.7], ["v1", "v2"], ["standard"])
    plan = select_plan(n_candidates=len(arms), arms=arms, arm_stats=[])
    assert sorted(plan, key=lambda a: (a.temperature, a.prompt_variant)) == sorted(
        arms, key=lambda a: (a.temperature, a.prompt_variant)
    )


def test_small_batch_samples_more_than_one_temperature():
    # Regression test: build_arm_space used to order arms temperature-major,
    # so a small n_candidates run never left the lowest temperature. The
    # diagonal ordering must vary temperature within the first few picks.
    arms = build_arm_space(MODEL, [0.3, 0.7, 1.0, 1.2], ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"], ["standard"])
    plan = select_plan(n_candidates=3, arms=arms, arm_stats=[])
    assert len({a.temperature for a in plan}) > 1


def test_cold_start_does_not_repeat_before_full_coverage():
    arms = build_arm_space(MODEL, [0.3, 0.7], ["v1", "v2"], ["standard"])
    plan = select_plan(n_candidates=3, arms=arms, arm_stats=[])
    assert len(set(plan)) == 3  # no repeats among the first len(arms)-1 picks


def test_exploits_the_higher_scoring_arm_once_all_arms_have_history():
    arms = [
        Arm(MODEL, 0.3, "v1", "standard"),
        Arm(MODEL, 0.7, "v2", "standard"),
    ]
    arm_stats = [
        {"model": MODEL, "temperature": 0.3, "prompt_variant": "v1",
         "reasoning_effort": "standard", "pulls": 20, "avg_score": 0.9},
        {"model": MODEL, "temperature": 0.7, "prompt_variant": "v2",
         "reasoning_effort": "standard", "pulls": 20, "avg_score": 0.1},
    ]
    plan = select_plan(n_candidates=10, arms=arms, arm_stats=arm_stats, exploration_constant=0.1)
    counts = {arm: plan.count(arm) for arm in arms}
    assert counts[arms[0]] > counts[arms[1]]
