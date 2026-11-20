"""Excavator exploration-signal aggregation: branch_point / dead_end signals
from individual candidates roll up into a cycle-level reseed recommendation,
surfaced to the human rather than acted on automatically."""

import json

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _payload(core_claim: str, exploration_signal: str, suggested_seeds=None) -> str:
    return json.dumps(
        {
            "domain": "mathematics",
            "core_claim": core_claim,
            "formal_statement": "forall x, P(x)",
            "intuition": "intuition text",
            "connections": ["algebra"],
            "self_assessed_novelty": "medium",
            "identified_weaknesses": "none",
            "exploration_signal": exploration_signal,
            "suggested_seeds": suggested_seeds or [],
            "self_rated_novelty": 0.7,
            "self_rated_rigor": 0.7,
            "claim_type": "structural",
        }
    )


class SequencedClaudeClient:
    """Returns one payload per call, in the order given, regardless of
    prompt/temperature — lets a test script an exact sequence of signals."""

    def __init__(self, payloads: list[str]):
        self._payloads = payloads
        self.calls = 0

    async def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
        thinking_budget_tokens: int | None = None,
        cache_prefix: str | None = None,
        **kwargs,
    ) -> GenerationResult:
        text = self._payloads[self.calls % len(self._payloads)]
        self.calls += 1
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


@pytest.fixture
def seed(ledger):
    s = Seed(
        text="a seed about additive number theory and unit fractions",
        domain="mathematics",
        type="human",
    )
    ledger.insert_seed(s)
    return s


@pytest.mark.asyncio
async def test_branch_point_recommends_reseed_with_suggestions(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("claim one about unit fraction decompositions", "continue"),
        _payload("claim two about unit fraction decompositions", "continue"),
        _payload(
            "claim three about unit fraction decompositions",
            "branch_point",
            ["explore the p-group case", "explore the infinite case"],
        ),
    ]
    client = SequencedClaudeClient(payloads)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    result = await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=3)

    assert result.branch_point_count == 1
    assert result.dead_end_count == 0
    assert result.suggested_seed_texts == ["explore the p-group case", "explore the infinite case"]
    assert result.reseed_recommended is True
    assert result.reseed_reason is not None
    assert "branching" in result.reseed_reason


@pytest.mark.asyncio
async def test_dead_end_majority_recommends_reseed(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("claim one about unit fraction decompositions", "dead_end"),
        _payload("claim two about unit fraction decompositions", "dead_end"),
        _payload("claim three about unit fraction decompositions", "dead_end"),
        _payload("claim four about unit fraction decompositions", "dead_end"),
        _payload("claim five about unit fraction decompositions", "continue"),
    ]
    client = SequencedClaudeClient(payloads)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    result = await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=5)

    assert result.dead_end_count == 4
    assert result.branch_point_count == 0
    assert result.reseed_recommended is True
    assert result.reseed_reason is not None
    assert "dead_end" in result.reseed_reason


@pytest.mark.asyncio
async def test_minority_dead_end_does_not_recommend_reseed(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("claim one about unit fraction decompositions", "dead_end"),
        _payload("claim two about unit fraction decompositions", "continue"),
        _payload("claim three about unit fraction decompositions", "continue"),
        _payload("claim four about unit fraction decompositions", "continue"),
        _payload("claim five about unit fraction decompositions", "continue"),
    ]
    client = SequencedClaudeClient(payloads)
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0

    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    result = await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=5)

    assert result.dead_end_count == 1
    assert result.reseed_recommended is False
    assert result.reseed_reason is None
