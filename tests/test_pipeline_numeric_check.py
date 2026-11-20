"""End-to-end: a candidate whose self-supplied numeric_check mechanically
fails must be zeroed out in the ledger regardless of how confident its own
self-ratings were -- this is the fix for the exact class of bug found by
hand in the real Erdos-Straus mining run (a plausible-looking formula that
was actually false)."""

import json

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _payload(core_claim: str, numeric_check, claim_type: str = "construction") -> str:
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
            "self_rated_novelty": 0.95,
            "self_rated_rigor": 0.95,
            "claim_type": claim_type,
            "numeric_check": numeric_check,
        }
    )


class FixedResponseClient:
    def __init__(self, text: str):
        self._text = text

    async def generate(
        self,
        prompt,
        temperature,
        max_tokens=None,
        thinking_budget_tokens=None,
        cache_prefix=None,
        **kwargs,
    ):
        return GenerationResult(text=self._text, input_tokens=10, output_tokens=10)

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


@pytest.mark.asyncio
async def test_failing_numeric_witness_zeroes_a_high_confidence_candidate(
    ledger, fingerprint_engine
):
    text = _payload(
        "A confident but false claim about a parametric unit fraction construction.",
        numeric_check={"assignments": {"n": 6721, "a": 13, "b": 7}, "equation": "n == a * b"},
    )
    seed = Seed(
        text="a seed about number theory and unit fractions", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(
        ledger, config, fingerprint_engine, client=FixedResponseClient(text)
    )
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["numeric_check_result"] == 0
    assert candidate["path_score"] == 0.0


@pytest.mark.asyncio
async def test_passing_numeric_witness_is_stored_and_boosts_score(ledger, fingerprint_engine):
    text = _payload(
        "A claim about a divisibility identity that holds for this example.",
        numeric_check={"assignments": {"n": 91, "a": 13, "b": 7}, "equation": "n == a * b"},
    )
    seed = Seed(
        text="a seed about number theory and divisibility", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(
        ledger, config, fingerprint_engine, client=FixedResponseClient(text)
    )
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["numeric_check_result"] == 1
    assert candidate["path_score"] == 1.0  # 0.95 avg * 1.2 bonus, capped


@pytest.mark.asyncio
async def test_no_numeric_witness_on_structural_claim_is_neutral(ledger, fingerprint_engine):
    text = _payload(
        "A purely qualitative structural claim with no concrete witness.",
        numeric_check=None,
        claim_type="structural",
    )
    seed = Seed(
        text="a seed about category theory and structure", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(
        ledger, config, fingerprint_engine, client=FixedResponseClient(text)
    )
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["numeric_check_result"] is None
    assert candidate["claim_type"] == "structural"
    assert candidate["path_score"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_construction_claim_without_witness_is_penalized(ledger, fingerprint_engine):
    text = _payload(
        "An explicit constructive claim that conveniently skips its required witness.",
        numeric_check=None,
        claim_type="construction",
    )
    seed = Seed(
        text="a seed about number theory and explicit constructions",
        domain="mathematics",
        type="human",
    )
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(
        ledger, config, fingerprint_engine, client=FixedResponseClient(text)
    )
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["path_score"] == pytest.approx(0.95 * 0.6)


@pytest.mark.asyncio
async def test_hollow_pass_on_asymptotic_claim_earns_no_bonus(ledger, fingerprint_engine):
    # The real failure mode observed in mining: an asymptotic complexity
    # claim with a witness that only confirms the model's own invented
    # numbers are self-consistent. It "passes" mechanically, but must earn
    # nothing -- one example cannot verify a growth-rate claim.
    text = _payload(
        "A big-O complexity claim about search-tree node counts as n grows.",
        numeric_check={
            "assignments": {"n": 10, "k": 2, "bound": 400},
            "equation": "n**2 * 2**k <= bound and bound == 400",
        },
        claim_type="asymptotic",
    )
    seed = Seed(
        text="a seed about graph theory search algorithm complexity",
        domain="mathematics",
        type="human",
    )
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(
        ledger, config, fingerprint_engine, client=FixedResponseClient(text)
    )
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["numeric_check_result"] == 1  # it did mechanically pass...
    assert candidate["path_score"] == pytest.approx(0.95)  # ...but no bonus applied
