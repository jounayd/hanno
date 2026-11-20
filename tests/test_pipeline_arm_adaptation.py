"""End-to-end proof that the pipeline actually learns: across two sequential
cycles on the same seed, an arm that consistently produces higher path_score
candidates should get selected more often in the second cycle than the
others — this is the whole point of replacing round-robin with a UCB1
bandit (arm_selection_strategy: ucb1, the default)."""

import json

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
WINNING_TEMP = 0.7
WINNING_VARIANT_HINT = "counterexample"  # substring unique to counterexample_construction's prompt


def _payload(core_claim: str, novelty: float, rigor: float) -> str:
    return json.dumps(
        {
            "domain": "mathematics",
            "core_claim": core_claim,
            "formal_statement": "forall x, P(x)",
            "intuition": "intuition text",
            "connections": ["algebra"],
            "self_assessed_novelty": "n/a",
            "identified_weaknesses": "n/a",
            "exploration_signal": "continue",
            "suggested_seeds": [],
            "self_rated_novelty": novelty,
            "self_rated_rigor": rigor,
            "claim_type": "structural",
        }
    )


class ArmAwareFakeClient:
    """Scores the (counterexample_construction, temp=0.7) arm highly and
    every other arm low, regardless of which candidate call it is."""

    def __init__(self):
        self.calls = 0

    async def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens=None,
        thinking_budget_tokens=None,
        cache_prefix=None,
        **kwargs,
    ) -> GenerationResult:
        self.calls += 1
        is_winner = temperature == WINNING_TEMP and WINNING_VARIANT_HINT in prompt.lower()
        if is_winner:
            core_claim = f"Winning candidate idea number {self.calls} about additive number theory structures."
            text = _payload(core_claim, novelty=0.95, rigor=0.95)
        else:
            core_claim = f"Losing candidate idea number {self.calls} about unrelated combinatorial constructions."
            text = _payload(core_claim, novelty=0.05, rigor=0.05)
        return GenerationResult(text=text, input_tokens=10, output_tokens=10)

    async def close(self) -> None:
        pass


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "test.db")
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def fingerprint_engine(ledger):
    config = FingerprintConfig(embedding_model=TEST_MODEL, compressed_dimensions=384)
    return FingerprintEngine(ledger, config)


def _winner_count(plan_candidates: list[dict]) -> int:
    return sum(
        1
        for c in plan_candidates
        if c["temperature"] == WINNING_TEMP and c["prompt_variant"] == "counterexample_construction"
    )


@pytest.mark.asyncio
async def test_bandit_favors_the_higher_scoring_arm_in_the_next_cycle(ledger, fingerprint_engine):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    config.excavator.temperatures = [0.3, 0.7]
    config.excavator.reasoning_efforts = ["standard"]  # keep the arm space small and fast

    seed = Seed(
        text="a seed about additive number theory and unit fractions",
        domain="mathematics",
        type="human",
    )
    ledger.insert_seed(seed)

    client = ArmAwareFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    # 2 temperatures x 8 variants = 16 arms -- cycle 1 covers all of them once (cold start).
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=16)

    arm_stats = ledger.get_arm_stats("mathematics")
    winner_stats = next(
        s
        for s in arm_stats
        if s["temperature"] == WINNING_TEMP and s["prompt_variant"] == "counterexample_construction"
    )
    assert winner_stats["avg_score"] > 0.9
    other_avgs = [s["avg_score"] for s in arm_stats if s is not winner_stats]
    assert all(winner_stats["avg_score"] > a for a in other_avgs)

    # Cycle 2: every arm now has equal history (1 pull each) except in average
    # score, which is where the winner should now visibly pull ahead.
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=16)
    candidates_cycle2 = ledger.list_candidates_by_seed(seed.id)[16:]
    assert len(candidates_cycle2) == 16

    winner_picks = _winner_count(candidates_cycle2)
    other_arm_pick_counts = {}
    for c in candidates_cycle2:
        key = (c["temperature"], c["prompt_variant"])
        other_arm_pick_counts[key] = other_arm_pick_counts.get(key, 0) + 1
    max_other_picks = max(
        (
            v
            for k, v in other_arm_pick_counts.items()
            if k != (WINNING_TEMP, "counterexample_construction")
        ),
        default=0,
    )

    assert winner_picks > 1  # picked more than its one fair "cold start" share
    assert winner_picks > max_other_picks  # and strictly more than any single other arm
