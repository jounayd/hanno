"""Fixes for the observed intra-cycle redundancy problem: a real mining run
produced ~10 near-identical reframings of the same mod-840 idea within a
single cycle, because (a) nothing steered candidates toward different
strategic angles, and (b) all candidates fired in one parallel batch with
zero visibility into each other. This tests both fixes:
  1. steering_hint rotates a distinct angle across candidate slots (always on).
  2. wave_size (opt-in, default 0/off) stages the cycle so later waves see
     earlier waves' own core_claims from the SAME cycle, not just prior cycles.
"""
import json

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.excavator.prompts import STEERING_HINTS
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class RecordingFakeClient:
    """Returns a distinct candidate each call and records the exact prompt
    text it was sent, so tests can inspect what context each call actually saw."""

    def __init__(self):
        self.calls = 0
        self.prompts_seen: list[str] = []

    async def generate(self, prompt: str, temperature: float, max_tokens=None, thinking_budget_tokens=None, cache_prefix=None) -> GenerationResult:
        self.calls += 1
        self.prompts_seen.append(prompt)
        text = json.dumps({
            "domain": "mathematics",
            "core_claim": f"Distinct generated claim number {self.calls} about additive number theory.",
            "formal_statement": "forall x, P(x)",
            "intuition": "intuition text",
            "connections": ["algebra"],
            "self_assessed_novelty": "n/a",
            "identified_weaknesses": "n/a",
            "exploration_signal": "continue",
            "suggested_seeds": [],
            "self_rated_novelty": 0.5,
            "self_rated_rigor": 0.5,
            "claim_type": "structural",
        })
        return GenerationResult(text=text, input_tokens=10, output_tokens=10)

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


@pytest.fixture
def seed(ledger):
    s = Seed(text="a seed about additive number theory and unit fractions", domain="mathematics", type="human")
    ledger.insert_seed(s)
    return s


@pytest.mark.asyncio
async def test_steering_hints_rotate_across_candidate_slots(ledger, fingerprint_engine, seed):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    client = RecordingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    n = len(STEERING_HINTS) + 2  # enough to prove both coverage and wraparound
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=n)

    candidates = ledger.list_candidates_by_seed(seed.id)
    hints_used = [c["steering_hint"] for c in candidates]
    assert set(hints_used) == set(STEERING_HINTS)  # full coverage
    assert hints_used[0] == hints_used[len(STEERING_HINTS)]  # wraps around deterministically


@pytest.mark.asyncio
async def test_default_wave_size_zero_gives_no_same_cycle_context(ledger, fingerprint_engine, seed):
    # Regression guard: the default (wave_size=0, full parallel) must not
    # change -- no candidate should see any OTHER candidate from the same cycle.
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    assert config.excavator.wave_size == 0

    client = RecordingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=5)

    for prompt in client.prompts_seen:
        assert "already been explored" not in prompt


@pytest.mark.asyncio
async def test_wave_size_gives_later_candidates_same_cycle_context(ledger, fingerprint_engine, seed):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    config.excavator.wave_size = 2  # 6 candidates -> 3 sequential waves of 2

    client = RecordingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=6)

    # The first wave's prompts can't have seen anything yet.
    assert "already been explored" not in client.prompts_seen[0]
    assert "already been explored" not in client.prompts_seen[1]

    # By the last wave, the prompt must reference claims produced earlier in
    # THIS SAME cycle -- not just prior cycles (there were none here).
    last_prompt = client.prompts_seen[-1]
    assert "already been explored" in last_prompt
    assert "Distinct generated claim number 1" in last_prompt


@pytest.mark.asyncio
async def test_wave_size_larger_than_plan_behaves_like_disabled(ledger, fingerprint_engine, seed):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    config.excavator.wave_size = 999  # larger than n_candidates -- effectively one wave

    client = RecordingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=5)

    for prompt in client.prompts_seen:
        assert "already been explored" not in prompt
