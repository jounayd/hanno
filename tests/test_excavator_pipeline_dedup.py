"""ExcavatorPipeline <-> FingerprintEngine wiring: candidates that repeat the
same core_claim across temperature/prompt-variant combinations must be
flagged EXACT_DUPLICATE (General-spec.txt:112-115), never silently dropped.
"""
import json

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _candidate_json(
    core_claim: str, exploration_signal: str = "continue", suggested_seeds=None,
    self_rated_novelty: float = 0.8, self_rated_rigor: float = 0.8,
) -> str:
    return json.dumps({
        "domain": "mathematics",
        "core_claim": core_claim,
        "formal_statement": "forall x, P(x)",
        "intuition": "intuition text",
        "connections": ["algebra"],
        "self_assessed_novelty": "medium",
        "identified_weaknesses": "none",
        "exploration_signal": exploration_signal,
        "suggested_seeds": suggested_seeds or [],
        "self_rated_novelty": self_rated_novelty,
        "self_rated_rigor": self_rated_rigor,
        "claim_type": "structural",
    })


class FakeClaudeClient:
    """Always returns the same candidate JSON, regardless of prompt/temp —
    simulates an Excavator that keeps rediscovering the same idea."""

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    async def generate(
        self, prompt: str, temperature: float,
        max_tokens: int | None = None, thinking_budget_tokens: int | None = None,
        cache_prefix: str | None = None,
    ) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text=self._text, input_tokens=100, output_tokens=100)

    async def close(self) -> None:
        pass


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


@pytest.fixture
def fingerprint_engine(ledger):
    config = FingerprintConfig(embedding_model=TEST_MODEL, compressed_dimensions=384)
    return FingerprintEngine(ledger, config)


@pytest.mark.asyncio
async def test_repeated_identical_candidates_flagged_exact_duplicate(ledger, fingerprint_engine):
    same_text = _candidate_json("Every finite simple group embeds into a bounded-degree symmetric group.")
    client = FakeClaudeClient(same_text)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    seed = Seed(text="a seed about finite simple groups and representation theory", domain="mathematics", type="human")
    ledger.insert_seed(seed)

    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    result = await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=4)

    assert result.candidates_parsed_ok == 4
    assert result.candidates_exact_duplicate == 3  # first is NOVEL, other 3 collide
    assert result.candidates_near_duplicate == 0

    candidates = ledger.list_candidates_by_seed(seed.id)
    statuses = sorted(c["duplicate_status"] for c in candidates)
    assert statuses == ["EXACT_DUPLICATE", "EXACT_DUPLICATE", "EXACT_DUPLICATE", "NOVEL"]

    assert result.total_input_tokens == 400
    assert result.total_output_tokens == 400
    assert all(c["input_tokens"] == 100 and c["output_tokens"] == 100 for c in candidates)
    assert all(seed.text in c["prompt_text"] for c in candidates)

    cycles = ledger.list_excavation_cycles()
    assert cycles[0]["total_input_tokens"] == 400
    assert cycles[0]["total_output_tokens"] == 400

    by_status = {c["duplicate_status"]: c for c in candidates}
    assert by_status["NOVEL"]["path_score"] == pytest.approx(0.8)
    assert by_status["EXACT_DUPLICATE"]["path_score"] == 0.0

    arm_stats = ledger.get_arm_stats("mathematics")
    assert sum(a["pulls"] for a in arm_stats) == 4


@pytest.mark.asyncio
async def test_second_cycle_prompts_include_prior_core_claims(ledger, fingerprint_engine):
    seed = Seed(text="a seed about finite simple groups and representation theory", domain="mathematics", type="human")
    ledger.insert_seed(seed)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    first_text = _candidate_json("A claim about Sylow subgroup embeddings in symmetric groups.")
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=FakeClaudeClient(first_text))
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    second_text = _candidate_json("A distinct claim about representation stability in symmetric groups.")
    pipeline2 = ExcavatorPipeline(ledger, config, fingerprint_engine, client=FakeClaudeClient(second_text))
    await pipeline2.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    candidates = ledger.list_candidates_by_seed(seed.id)
    second_cycle_candidate = candidates[-1]
    assert "already been explored" in second_cycle_candidate["prompt_text"]
    assert "Sylow subgroup embeddings" in second_cycle_candidate["prompt_text"]
