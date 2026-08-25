"""run_iterative_mining: beam search over the idea space. Each iteration
expands every active thread, pools all resulting candidates, and keeps the
global top_k eligible ones (by path_score) as the next iteration's beam.
Human reseed should only be recommended when the best-of-beam score has
stayed high for several consecutive iterations -- not on a single lucky cycle.
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


def _payload(core_claim, novelty, rigor, exploration_signal="continue"):
    return json.dumps({
        "domain": "mathematics", "core_claim": core_claim, "formal_statement": "forall x, P(x)",
        "intuition": "intuition text", "connections": ["algebra"],
        "self_assessed_novelty": "n/a", "identified_weaknesses": "n/a",
        "exploration_signal": exploration_signal, "suggested_seeds": [],
        "self_rated_novelty": novelty, "self_rated_rigor": rigor,
        "claim_type": "structural",
    })


class ScriptedClient:
    """Returns exactly one scripted payload per call, in order. Tests use
    n_candidates_per_iteration=1 so each thread-cycle is exactly one call,
    making the call sequence fully deterministic and easy to script."""

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


def _config(**overrides):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    # These tests script exact per-iteration call counts, so the wildcat
    # exploration thread (default on) is disabled here; it has its own
    # dedicated tests below.
    config.excavator.iteration_exploration_thread = False
    for k, v in overrides.items():
        setattr(config.excavator, k, v)
    return config


@pytest.mark.asyncio
async def test_depth_one_runs_exactly_one_cycle_no_strong_path(ledger, fingerprint_engine, seed):
    payloads = [_payload("a distinct claim about additive structure", 0.9, 0.9)]
    client = ScriptedClient(payloads)
    config = _config()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    assert len(result.iterations) == 1
    assert client.calls == 1
    assert result.strong_path_found is False  # only 1 iteration -- can't be "sustained"


@pytest.mark.asyncio
async def test_beam_keeps_top_k_eligible_candidates(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("weak claim about a dead end", 0.1, 0.1, exploration_signal="dead_end"),
        _payload("medium claim about residues", 0.5, 0.5),
        _payload("strong claim about density arguments", 0.9, 0.9),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=3, depth=1)

    beam = result.iterations[0].beam_after
    assert len(beam) == 2  # top_k=2, and the dead_end one is ineligible regardless of rank
    scores = sorted(t.best_path_score for t in beam)
    assert scores == pytest.approx([0.5, 0.9])


@pytest.mark.asyncio
async def test_iteration_two_refines_each_surviving_thread_independently(ledger, fingerprint_engine, seed):
    # Iteration 1: 1 call -> 1 winner. Iteration 2: top_k=2 requested, but
    # only 1 thread exists after iteration 1, so exactly 1 call happens.
    payloads = [
        _payload("iteration 1 winner about additive bases", 0.9, 0.9),
        _payload("iteration 2 refinement building on the winner", 0.95, 0.95),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert len(result.iterations) == 2
    assert client.calls == 2
    iter1_winner_id = result.iterations[0].beam_after[0].lineage[-1]
    iter2_candidates = ledger.list_candidates_by_seed(seed.id)
    iter2_candidate = [c for c in iter2_candidates if c["iteration"] == 2][0]
    assert iter2_candidate["refined_from_candidate_id"] == iter1_winner_id
    assert "iteration 1 winner" in iter2_candidate["prompt_text"]  # refined seed text carried the parent forward


@pytest.mark.asyncio
async def test_stops_early_when_nothing_eligible(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("everything here is a dead end", 0.1, 0.1, exploration_signal="dead_end"),
        _payload("this call should never happen", 0.9, 0.9),
    ]
    client = ScriptedClient(payloads)
    config = _config()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)

    assert client.calls == 1  # never reached iteration 2
    assert len(result.iterations) == 1
    assert result.iterations[0].stopped_early is True
    assert result.strong_path_found is False


@pytest.mark.asyncio
async def test_strong_path_requires_sustained_high_score(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("iteration 1: promising start", 0.65, 0.65),
        _payload("iteration 2: still strong", 0.7, 0.7),
        _payload("iteration 3: confirmed strong", 0.75, 0.75),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1, strong_path_score_threshold=0.6, min_iterations_for_reseed=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)

    assert result.strong_path_found is True
    assert "sustained" in result.strong_path_reason
    assert len(result.best_lineage) == 3


@pytest.mark.asyncio
async def test_parse_failed_candidate_is_not_eligible_to_carry_forward(ledger, fingerprint_engine, seed):
    # Regression test: a parse failure has core_claim=None, duplicate_status=None,
    # exploration_signal=None -- which used to vacuously pass every eligibility
    # check (None != "EXACT_DUPLICATE", None != "dead_end", None != 0). If it's
    # the only "surviving" candidate in an iteration, the run must stop early
    # rather than build a refinement prompt around a candidate with no content.
    payloads = [
        _payload("a dead end claim", 0.5, 0.5, exploration_signal="dead_end"),
        "{ this is not valid json at all",  # triggers a parse failure
        "this call should never happen",
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=2, depth=3)

    assert client.calls == 2  # never reached iteration 2 (or the 3rd scripted call)
    assert len(result.iterations) == 1
    assert result.iterations[0].stopped_early is True
    assert result.iterations[0].beam_after == []


@pytest.mark.asyncio
async def test_single_high_iteration_is_not_enough_for_strong_path(ledger, fingerprint_engine, seed):
    # One great iteration followed by a mediocre one must NOT trigger --
    # this is exactly the "don't ask on a single lucky cycle" requirement.
    payloads = [
        _payload("iteration 1: a lucky high score", 0.95, 0.95),
        _payload("iteration 2: regression to the mean", 0.3, 0.3),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1, strong_path_score_threshold=0.6, min_iterations_for_reseed=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert result.strong_path_found is False


@pytest.mark.asyncio
async def test_wildcat_thread_mines_the_original_seed_each_iteration(ledger, fingerprint_engine, seed):
    # With the exploration thread ON, iteration 2 runs the beam thread PLUS
    # one fresh unrefined pass at the original seed, competing for beam slots.
    payloads = [
        _payload("iteration 1 winner about additive bases", 0.9, 0.9),
        _payload("iteration 2 refinement of the winner", 0.8, 0.8),
        _payload("iteration 2 wildcat fresh idea about modular forms", 0.7, 0.7),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1)
    config.excavator.iteration_exploration_thread = True
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert client.calls == 3  # 1 (iter1) + 2 (iter2: beam + wildcat)
    assert len(result.iterations[1].cycle_results) == 2

    iter2_candidates = [c for c in ledger.list_candidates_by_seed(seed.id) if c["iteration"] == 2]
    parents = {c["refined_from_candidate_id"] for c in iter2_candidates}
    assert None in parents  # the wildcat thread has no refinement parent
    assert any(p is not None for p in parents)  # the beam thread does
    wildcat = next(c for c in iter2_candidates if c["refined_from_candidate_id"] is None)
    assert "refinement round" not in wildcat["prompt_text"]


@pytest.mark.asyncio
async def test_beam_diversity_rejects_near_identical_beam_members(ledger, fingerprint_engine, seed):
    # Two high-scoring candidates that are paraphrases of the same idea must
    # not occupy both beam slots -- the beam shrinks instead.
    paraphrase_a = (
        "Investigate whether every finite simple group embeds into a symmetric "
        "group of bounded degree, connecting to representation theory and algebra."
    )
    paraphrase_b = (
        "Could each finite simple group be embedded within a symmetric group whose "
        "degree stays bounded, drawing on representation theory and algebraic structure?"
    )
    payloads = [
        _payload(paraphrase_a, 0.9, 0.9),
        _payload(paraphrase_b, 0.85, 0.85),
        _payload("a genuinely unrelated claim about prime gaps and sieve methods", 0.1, 0.1,
                 exploration_signal="dead_end"),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=2)
    config.excavator.beam_diversity_max_similarity = 0.7  # paraphrase pair sits above this
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=3, depth=1)

    beam = result.iterations[0].beam_after
    assert len(beam) == 1  # the paraphrase was rejected, dead_end ineligible
    assert beam[0].best_path_score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_beam_diversity_disabled_keeps_both_paraphrases(ledger, fingerprint_engine, seed):
    paraphrase_a = (
        "Investigate whether every finite simple group embeds into a symmetric "
        "group of bounded degree, connecting to representation theory and algebra."
    )
    paraphrase_b = (
        "Could each finite simple group be embedded within a symmetric group whose "
        "degree stays bounded, drawing on representation theory and algebraic structure?"
    )
    payloads = [_payload(paraphrase_a, 0.9, 0.9), _payload(paraphrase_b, 0.85, 0.85)]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=2)
    config.excavator.beam_diversity_max_similarity = 1.0  # filter disabled
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=2, depth=1)

    assert len(result.iterations[0].beam_after) == 2


@pytest.mark.asyncio
async def test_plateau_stops_early_when_best_score_stops_improving(ledger, fingerprint_engine, seed):
    payloads = [
        _payload("iteration 1: strong start about additive bases", 0.9, 0.9),
        _payload("iteration 2: no improvement over iteration 1", 0.5, 0.5),
        _payload("iteration 3: should never run", 0.95, 0.95),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1, iteration_plateau_patience=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)

    assert client.calls == 2  # iteration 3 never ran
    assert len(result.iterations) == 2
    assert result.iterations[1].stopped_early is True
    assert "plateau" in result.iterations[1].stop_reason


@pytest.mark.asyncio
async def test_second_run_resumes_iteration_numbering_and_beam(ledger, fingerprint_engine, seed):
    # Run 1: depth 3. Run 2: depth 2. The seed's ledger history must read as
    # one cumulative search: iterations 1-5, with iteration 4 refining the
    # best candidate of iteration 3 -- not a restart at iteration 1.
    run1_payloads = [
        _payload("iteration 1 idea about unit fractions", 0.7, 0.7),
        _payload("iteration 2 deepening the decomposition bound", 0.75, 0.75),
        _payload("iteration 3 sharpened denominator estimate", 0.8, 0.8),
    ]
    config = _config(iteration_top_k=1)
    pipeline1 = ExcavatorPipeline(ledger, config, fingerprint_engine, client=ScriptedClient(run1_payloads))
    result1 = await pipeline1.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)
    assert result1.resumed_from_iteration is None
    run1_best_id = result1.iterations[-1].beam_after[0].lineage[-1]

    run2_payloads = [
        _payload("iteration 4 refinement building on the estimate", 0.85, 0.85),
        _payload("iteration 5 final tightening of the argument", 0.9, 0.9),
    ]
    pipeline2 = ExcavatorPipeline(ledger, config, fingerprint_engine, client=ScriptedClient(run2_payloads))
    result2 = await pipeline2.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert result2.resumed_from_iteration == 3
    assert [s.iteration for s in result2.iterations] == [4, 5]

    candidates = ledger.list_candidates_by_seed(seed.id)
    assert sorted(c["iteration"] for c in candidates) == [1, 2, 3, 4, 5]
    iter4 = next(c for c in candidates if c["iteration"] == 4)
    assert iter4["refined_from_candidate_id"] == run1_best_id
    # The resumed lineage spans both runs: 3 candidates from run 1 + 2 from run 2.
    assert len(result2.best_lineage) == 5


@pytest.mark.asyncio
async def test_fresh_run_restarts_at_iteration_one(ledger, fingerprint_engine, seed):
    config = _config(iteration_top_k=1)
    pipeline1 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("first run claim about density", 0.7, 0.7)]),
    )
    await pipeline1.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    pipeline2 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("second run claim about congruences", 0.8, 0.8)]),
    )
    result = await pipeline2.run_iterative_mining(
        seed.id, seed.text, n_candidates_per_iteration=1, depth=1, resume=False,
    )

    assert result.resumed_from_iteration is None
    assert [s.iteration for s in result.iterations] == [1]
    new_candidate = ledger.list_candidates_by_seed(seed.id)[-1]
    assert new_candidate["iteration"] == 1
    assert new_candidate["refined_from_candidate_id"] is None


@pytest.mark.asyncio
async def test_resume_walks_back_past_ineligible_last_iteration(ledger, fingerprint_engine, seed):
    # Run 1 ends on an iteration whose only candidate is a dead end. Resume
    # must rebuild the beam from the last iteration that still has an
    # eligible candidate (iteration 1), while numbering continues after the
    # dead-end iteration.
    run1_payloads = [
        _payload("iteration 1 solid claim about partitions", 0.8, 0.8),
        _payload("iteration 2 turned out to be a dead end", 0.2, 0.2, exploration_signal="dead_end"),
    ]
    config = _config(iteration_top_k=1)
    pipeline1 = ExcavatorPipeline(ledger, config, fingerprint_engine, client=ScriptedClient(run1_payloads))
    result1 = await pipeline1.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)
    iter1_id = result1.iterations[0].beam_after[0].lineage[-1]

    pipeline2 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("iteration 3 pivots off the solid claim", 0.85, 0.85)]),
    )
    result2 = await pipeline2.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    assert result2.resumed_from_iteration == 2
    assert [s.iteration for s in result2.iterations] == [3]
    iter3 = next(c for c in ledger.list_candidates_by_seed(seed.id) if c["iteration"] == 3)
    assert iter3["refined_from_candidate_id"] == iter1_id


@pytest.mark.asyncio
async def test_resume_with_no_eligible_history_falls_back_to_original_seed(ledger, fingerprint_engine, seed):
    # Every prior candidate is ineligible: numbering still continues (the
    # ledger sequence stays cumulative) but the beam falls back to mining
    # the original seed, with no refinement parent.
    config = _config(iteration_top_k=1)
    pipeline1 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("only a dead end here", 0.1, 0.1, exploration_signal="dead_end")]),
    )
    await pipeline1.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    pipeline2 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("a fresh angle on the original seed", 0.7, 0.7)]),
    )
    result = await pipeline2.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    assert result.resumed_from_iteration == 1
    assert [s.iteration for s in result.iterations] == [2]
    iter2 = next(c for c in ledger.list_candidates_by_seed(seed.id) if c["iteration"] == 2)
    assert iter2["refined_from_candidate_id"] is None
    assert "refinement round" not in iter2["prompt_text"]


@pytest.mark.asyncio
async def test_strong_path_can_span_runs(ledger, fingerprint_engine, seed):
    # Sustained-high-score detection must treat resumed history as part of
    # the sequence: 1 strong iteration in run 1 + 1 strong iteration in run 2
    # satisfies min_iterations_for_reseed=2 even though each run alone would not.
    config = _config(iteration_top_k=1, strong_path_score_threshold=0.6, min_iterations_for_reseed=2)
    pipeline1 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("run 1: strong opening claim", 0.7, 0.7)]),
    )
    result1 = await pipeline1.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)
    assert result1.strong_path_found is False

    pipeline2 = ExcavatorPipeline(
        ledger, config, fingerprint_engine,
        client=ScriptedClient([_payload("run 2: refinement stays strong", 0.75, 0.75)]),
    )
    result2 = await pipeline2.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=1)

    assert result2.strong_path_found is True


@pytest.mark.asyncio
async def test_promising_lineage_survives_a_weak_refinement_round(ledger, fingerprint_engine, seed):
    # Iteration 1: a strong candidate (0.8) establishes a promising lineage.
    # Iteration 2: refining it produces something weaker (0.3) -- below
    # retention default (0.5), the OLD behavior would have dropped the
    # lineage entirely for this weaker child. With retention, the lineage's
    # own best candidate (0.8) stays in the pool and wins the beam slot back,
    # instead of the search being forced onto the worse 0.3 candidate.
    payloads = [
        _payload("iteration 1: a genuinely strong claim about partitions", 0.8, 0.8),
        _payload("iteration 2: a weak refinement attempt that undersells it", 0.3, 0.3),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1)  # retention threshold defaults to 0.5
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert client.calls == 2  # the weak refinement attempt still ran and cost money
    iter1_winner_id = result.iterations[0].beam_after[0].lineage[-1]
    iter2_beam = result.iterations[1].beam_after
    assert len(iter2_beam) == 1
    # Retained: same candidate id, same score -- NOT the weak 0.3 child.
    assert iter2_beam[0].lineage[-1] == iter1_winner_id
    assert iter2_beam[0].best_path_score == pytest.approx(0.8)
    assert not result.iterations[1].stopped_early


@pytest.mark.asyncio
async def test_retained_lineage_keeps_trying_and_can_still_improve_later(ledger, fingerprint_engine, seed):
    # A retained lineage isn't frozen -- it gets refined again next
    # iteration, and if THAT attempt genuinely beats the anchor, the beam
    # advances normally.
    payloads = [
        _payload("iteration 1: strong opening claim about sequences", 0.8, 0.8),
        _payload("iteration 2: a weak attempt, retained instead of replacing it", 0.3, 0.3),
        _payload("iteration 3: this refinement finally improves on the anchor", 0.9, 0.9),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)

    assert client.calls == 3
    assert len(result.iterations) == 3
    final_beam = result.iterations[2].beam_after
    assert final_beam[0].best_path_score == pytest.approx(0.9)
    iter1_id = result.iterations[0].beam_after[0].lineage[-1]
    assert final_beam[0].lineage[-1] != iter1_id  # advanced to the genuinely better iteration-3 candidate
    assert final_beam[0].lineage[0] == iter1_id  # but the lineage history still starts from iteration 1


@pytest.mark.asyncio
async def test_retention_never_beats_a_genuinely_better_new_idea(ledger, fingerprint_engine, seed):
    # Retention only protects against being replaced by something WORSE. A
    # better independent idea (here, the wildcat) still wins the slot fairly.
    payloads = [
        _payload("iteration 1: a decent claim about lattice points", 0.55, 0.55),
        _payload("iteration 2: refinement is weaker than the anchor", 0.2, 0.2),
        _payload("iteration 2: wildcat finds something better", 0.85, 0.85),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1)
    config.excavator.iteration_exploration_thread = True
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    assert result.iterations[1].beam_after[0].best_path_score == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_below_retention_threshold_lineage_is_not_protected(ledger, fingerprint_engine, seed):
    # Retention only kicks in above iteration_lineage_retention_threshold --
    # a mediocre lineage below the bar can still be dropped for a weak
    # refinement, same as before this feature existed.
    payloads = [
        _payload("iteration 1: a mediocre claim about polynomials", 0.4, 0.4),
        _payload("iteration 2: an even weaker refinement", 0.1, 0.1),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1, iteration_lineage_retention_threshold=0.5)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=2)

    # 0.4 < 0.5 retention threshold -- not protected, beam moves to the weak child.
    assert result.iterations[1].beam_after[0].best_path_score == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_strong_path_requires_consecutive_fresh_confirmations_not_retention(ledger, fingerprint_engine, seed):
    # A single high score that then gets "protected" by retention across
    # several idle iterations must NOT count as sustained progress -- only
    # genuinely fresh, independently-generated high scores in a row do.
    payloads = [
        _payload("iteration 1: a lucky high score", 0.9, 0.9),
        _payload("iteration 2: fails to reconfirm, retained instead", 0.2, 0.2),
        _payload("iteration 3: also fails to reconfirm, retained instead", 0.3, 0.3),
    ]
    client = ScriptedClient(payloads)
    config = _config(iteration_top_k=1, strong_path_score_threshold=0.6, min_iterations_for_reseed=2)
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=1, depth=3)

    assert result.strong_path_found is False


@pytest.mark.asyncio
async def test_strong_path_lineage_reported_may_differ_from_raw_best_score(ledger, fingerprint_engine, seed):
    # With top_k=2, one thread sustains two consecutive passes at/above
    # threshold (genuine streak); another thread spikes higher just once via
    # the wildcat. STRONG PATH FOUND must credit the sustained lineage, not
    # just whichever thread happens to have the single highest raw score.
    # n_candidates_per_iteration=2 so iteration 1's single seed-thread cycle
    # produces two distinct candidates (thread A and B both enter the beam);
    # each of the 3 iteration-2 threads (A, B, wildcat) then also requests 2
    # candidates, with a low-scoring filler in each pair so it never affects
    # top-k selection.
    payloads = [
        _payload("thread A: steady start about partitions", 0.65, 0.65),
        _payload("thread B: also promising, about lattice sums", 0.62, 0.62),
        _payload("thread A refinement: stays strong", 0.68, 0.68),
        _payload("thread A filler, irrelevant low score", 0.05, 0.05, exploration_signal="dead_end"),
        _payload("thread B refinement: drops off badly", 0.1, 0.1),
        _payload("thread B filler, irrelevant low score", 0.08, 0.08, exploration_signal="dead_end"),
        _payload("wildcat: one-off spike about modular forms, no track record", 0.95, 0.95),
        _payload("wildcat filler, irrelevant low score", 0.02, 0.02, exploration_signal="dead_end"),
    ]
    client = ScriptedClient(payloads)
    config = _config(
        iteration_top_k=2, strong_path_score_threshold=0.6, min_iterations_for_reseed=2,
        iteration_lineage_retention_threshold=0.0,  # isolate the streak logic from retention here
    )
    config.excavator.iteration_exploration_thread = True
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client)

    result = await pipeline.run_iterative_mining(seed.id, seed.text, n_candidates_per_iteration=2, depth=2)

    assert client.calls == 8
    assert result.strong_path_found is True
    # The reported lineage is thread A's two-candidate chain, not the
    # single-candidate wildcat spike (0.95 > 0.68, but no sustained streak).
    assert len(result.best_lineage) == 2
    winning_candidates = ledger.list_candidates_by_seed(seed.id)
    winner = next(c for c in winning_candidates if c["id"] == result.best_lineage[-1])
    assert winner["path_score"] == pytest.approx(0.68)
