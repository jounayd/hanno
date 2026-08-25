"""run_iterative_mining(until_promising=True): the live Lean-check stop
condition added on top of the beam search. Hermetic -- autoformalize_with_retry
is patched, so this suite never needs the real Lean toolchain or a live API
key (that only runs in the manual pilot / real mining)."""
import json
from pathlib import Path

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.lean.autoformalize import AutoformalizeWithCheckResult
from hanno.lean.checker import CheckResult
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _payload(core_claim, novelty=0.7, rigor=0.7):
    return json.dumps({
        "domain": "mathematics", "core_claim": core_claim, "formal_statement": "forall x, P(x)",
        "intuition": "intuition text", "connections": ["algebra"],
        "self_assessed_novelty": "n/a", "identified_weaknesses": "n/a",
        "exploration_signal": "continue", "suggested_seeds": [],
        "self_rated_novelty": novelty, "self_rated_rigor": rigor,
        "claim_type": "structural",
    })


class ScriptedClient:
    def __init__(self, payloads: list[str]):
        self._payloads = payloads
        self.calls = 0

    async def generate(self, prompt, temperature, max_tokens=None, thinking_budget_tokens=None, cache_prefix=None):
        text = self._payloads[self.calls]
        self.calls += 1
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


def _config(lean_project_dir, **overrides):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    config.excavator.iteration_exploration_thread = False
    config.excavator.strong_path_score_threshold = 0.6
    config.lean_verifier.mathlib_cache_path = str(lean_project_dir)
    for k, v in overrides.items():
        setattr(config.excavator, k, v)
    return config


def _survivor_outcome(lean_code="theorem foo : True := by sorry"):
    return AutoformalizeWithCheckResult(
        lean_code=lean_code,
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=100),
        attempts=1,
    )


def _malformed_outcome():
    return AutoformalizeWithCheckResult(
        lean_code="theorem foo : Bogus := by sorry",
        check=CheckResult(compiles=False, trivial_by=None, stdout="error", stderr="", duration_ms=100),
        attempts=3,
    )


@pytest.mark.asyncio
async def test_stops_early_on_first_survivor_and_records_both_ledger_rows(
    ledger, fingerprint_engine, seed, tmp_path, monkeypatch,
):
    lean_dir = tmp_path / "mathlib_cache"
    lean_dir.mkdir()
    payloads = [
        _payload("iteration 1: a promising claim"),
        _payload("iteration 2: should never be reached"),
        _payload("iteration 3: should never be reached"),
    ]
    client = ScriptedClient(payloads)
    config = _config(lean_dir, iteration_top_k=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        assert project_dir == lean_dir
        return _survivor_outcome()

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    result = await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=1, depth=3, until_promising=True,
    )

    assert client.calls == 1  # never reached iteration 2 or 3
    assert result.promising_found is True
    assert result.promising_candidate_id is not None
    assert "triviality filter" in result.promising_reason
    assert len(result.iterations) == 1
    assert result.iterations[0].stopped_early is True

    verifications = ledger.list_lean_verifications_by_candidate(result.promising_candidate_id)
    assert len(verifications) == 1
    assert verifications[0]["result"] is None  # a survivor -- never trivially closed

    discoveries = ledger.list_discoveries()
    assert len(discoveries) == 1
    assert discoveries[0]["candidate_id"] == result.promising_candidate_id
    assert discoveries[0]["lean_verified"] == 1
    assert discoveries[0]["human_reviewed"] == 0


@pytest.mark.asyncio
async def test_runs_to_full_depth_when_never_promising(ledger, fingerprint_engine, seed, tmp_path, monkeypatch):
    lean_dir = tmp_path / "mathlib_cache"
    lean_dir.mkdir()
    payloads = [_payload(f"iteration {i}: a claim") for i in range(1, 4)]
    client = ScriptedClient(payloads)
    config = _config(lean_dir, iteration_top_k=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        return _malformed_outcome()

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    result = await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=1, depth=3, until_promising=True,
    )

    assert client.calls == 3
    assert result.promising_found is False
    assert result.promising_candidate_id is None
    assert len(result.iterations) == 3
    assert ledger.list_discoveries() == []


@pytest.mark.asyncio
async def test_does_not_lean_check_when_below_score_threshold(ledger, fingerprint_engine, seed, tmp_path, monkeypatch):
    lean_dir = tmp_path / "mathlib_cache"
    lean_dir.mkdir()
    payloads = [_payload("a mediocre claim", novelty=0.3, rigor=0.3)]
    client = ScriptedClient(payloads)
    config = _config(lean_dir, iteration_top_k=1, strong_path_score_threshold=0.9)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    calls = {"n": 0}

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        calls["n"] += 1
        return _survivor_outcome()

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    result = await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=1, depth=1, until_promising=True,
    )

    assert calls["n"] == 0  # score never cleared the (deliberately raised) threshold
    assert result.promising_found is False
    assert ledger.list_lean_verifications_by_candidate is not None  # sanity: method exists
    assert ledger.list_discoveries() == []


@pytest.mark.asyncio
async def test_checks_multiple_eligible_candidates_not_just_the_top_scoring_one(
    ledger, fingerprint_engine, seed, tmp_path, monkeypatch,
):
    """The starvation fix: previously only the single highest-path_score
    candidate per iteration ever got Lean-checked. Self-rated path_score
    isn't a trustworthy enough signal to gate the entire check budget down
    to one winner -- a higher-scoring but wrong candidate must not crowd
    out a second, still-eligible candidate from getting its own shot."""
    lean_dir = tmp_path / "mathlib_cache"
    lean_dir.mkdir()
    payloads = [
        _payload("a vague but confidently self-rated claim", novelty=0.9, rigor=0.9),
        _payload("a genuinely well-formed claim, rated more modestly", novelty=0.65, rigor=0.65),
    ]
    client = ScriptedClient(payloads)
    config = _config(lean_dir, iteration_top_k=2, max_lean_checks_per_iteration=3)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        if "vague" in candidate["core_claim"]:
            return _malformed_outcome()  # the higher-scoring candidate -- not a survivor
        return _survivor_outcome()  # the lower-scoring candidate -- a genuine survivor

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    result = await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=2, depth=1, until_promising=True,
    )

    assert result.promising_found is True
    candidates = ledger.list_candidates_by_seed(seed.id)
    assert len(candidates) == 2
    total_verifications = sum(
        len(ledger.list_lean_verifications_by_candidate(c["id"])) for c in candidates
    )
    assert total_verifications == 2  # both got checked, not just the top scorer
    assert len(ledger.list_discoveries()) == 1


@pytest.mark.asyncio
async def test_max_lean_checks_per_iteration_caps_the_budget(
    ledger, fingerprint_engine, seed, tmp_path, monkeypatch,
):
    lean_dir = tmp_path / "mathlib_cache"
    lean_dir.mkdir()
    payloads = [
        _payload("first eligible claim", novelty=0.9, rigor=0.9),
        _payload("second eligible claim", novelty=0.8, rigor=0.8),
    ]
    client = ScriptedClient(payloads)
    config = _config(lean_dir, iteration_top_k=2, max_lean_checks_per_iteration=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    calls = {"n": 0}

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        calls["n"] += 1
        return _malformed_outcome()  # neither is a survivor -- isolates the cap, not early-stop-on-survivor

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=2, depth=1, until_promising=True,
    )

    assert calls["n"] == 1  # capped at max_lean_checks_per_iteration despite 2 eligible candidates


@pytest.mark.asyncio
async def test_skips_check_when_lean_project_dir_missing(ledger, fingerprint_engine, seed, tmp_path, monkeypatch):
    missing_dir = tmp_path / "does_not_exist"
    payloads = [_payload("a promising claim")]
    client = ScriptedClient(payloads)
    config = _config(missing_dir, iteration_top_k=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    calls = {"n": 0}

    async def fake_autoformalize_with_retry(candidate, client_, project_dir, timeout_s, max_attempts):
        calls["n"] += 1
        return _survivor_outcome()

    monkeypatch.setattr(
        "hanno.excavator.pipeline.autoformalize_with_retry", fake_autoformalize_with_retry,
    )

    result = await pipeline.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=1, depth=1, until_promising=True,
    )

    assert calls["n"] == 0  # project dir missing -- checked and skipped, no crash
    assert result.promising_found is False
