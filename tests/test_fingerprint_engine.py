"""Fingerprint Engine (Module 3): concept-level uniqueness for both seeds
and candidates, per General-spec.txt:493-593.

Uses a real sentence-transformers model (downloaded on first run) — these
tests exercise semantic similarity, not string matching, on purpose:
paraphrases of the same idea must collide even with near-zero lexical
overlap.
"""

import pytest

from hanno.config import FingerprintConfig
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.repository import Ledger

# Small model, downloaded once and cached by sentence-transformers/HF hub.
TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

pytestmark = pytest.mark.embeddings


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "test.db")
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def engine(ledger):
    # compressed_dimensions=384 (== all-MiniLM-L6-v2's native output) makes
    # the pre-calibration "raw truncation" step a no-op, so these tests
    # measure the real embedding's semantic similarity rather than the
    # precision loss that raw truncation deliberately introduces before
    # enough fingerprints exist to fit a real PCA model.
    #
    # similarity_threshold_near_duplicate is lowered from the spec's
    # production default (0.92) to 0.75: General-spec.txt:585-592 is
    # explicit that the threshold is "a parameter, not a fixed truth" to
    # be calibrated per embedding model. all-MiniLM-L6-v2 is a small model
    # chosen for fast local tests and produces paraphrase similarities in
    # the 0.8-0.85 range rather than 0.92+ — a larger production model
    # would be calibrated separately.
    config = FingerprintConfig(
        embedding_model=TEST_MODEL,
        compressed_dimensions=384,
        similarity_threshold_near_duplicate=0.75,
    )
    return FingerprintEngine(ledger, config)


def test_first_fingerprint_is_always_novel(engine):
    result = engine.process("seed", "seed-1", "Explore connections between entropy and topology.")
    assert result.classification == "NOVEL"
    assert result.matched_hash is None


def test_identical_text_is_exact_duplicate(engine):
    text = "Investigate whether every finite simple group embeds into a symmetric group of bounded degree."
    engine.process("seed", "seed-1", text)
    result = engine.process("seed", "seed-2", text)
    assert result.classification == "EXACT_DUPLICATE"
    assert result.matched_entity_id == "seed-1"


def test_paraphrase_is_near_or_exact_duplicate_despite_different_wording(engine):
    # Same underlying concept, almost no shared vocabulary or word order.
    original = "Entropy functors in category theory might admit a natural transformation to probability monads."
    paraphrase = "There could be a canonical map from information-theoretic functors into monads of probability, viewed categorically."
    engine.process("seed", "seed-1", original)
    result = engine.process("seed", "seed-2", paraphrase)
    assert result.classification in ("NEAR_DUPLICATE", "EXACT_DUPLICATE")
    assert result.matched_entity_id == "seed-1"


def test_unrelated_concepts_are_novel(engine):
    engine.process(
        "seed", "seed-1", "Investigate counterexamples to the generalized Riemann hypothesis."
    )
    result = engine.process(
        "seed", "seed-2", "Propose a falsifiable mechanism for dark matter self-interaction."
    )
    assert result.classification == "NOVEL"


def test_seed_and_candidate_pools_are_compared_separately(engine):
    text = "Explore connections between entropy functors and probability monads."
    engine.process("seed", "seed-1", text)
    # Same text submitted as a candidate must not collide with the seed pool.
    result = engine.process("candidate", "cand-1", text)
    assert result.classification == "NOVEL"


def test_fingerprints_and_collision_events_are_persisted(engine, ledger):
    engine.process("seed", "seed-1", "A seed about algebraic topology and homotopy invariants.")
    fingerprints = ledger.list_fingerprints_by_entity_type("seed")
    assert len(fingerprints) == 1
    assert fingerprints[0]["entity_id"] == "seed-1"

    events = ledger._conn.execute("SELECT * FROM collision_events").fetchall()
    assert len(events) == 1
