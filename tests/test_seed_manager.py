"""SeedManager integration with the Fingerprint Engine for concept-level
seed uniqueness (flagged, never auto-rejected, per General-spec.txt:343-347)."""
import pytest

from hanno.config import FingerprintConfig
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.repository import Ledger
from hanno.seeds.manager import SeedManager

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

MATH_SEED_A = (
    "Investigate whether every finite simple group embeds into a symmetric "
    "group of bounded degree, connecting to representation theory and algebra."
)
MATH_SEED_A_PARAPHRASE = (
    "Could each finite simple group be embedded within a symmetric group whose "
    "degree stays bounded, drawing on representation theory and algebraic structure?"
)
MATH_SEED_B = (
    "Explore possible generalizations of the classification theorem for finite "
    "simple groups to infinite discrete groups with a topology, connecting to algebra."
)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


@pytest.fixture
def manager(ledger):
    # 384 == all-MiniLM-L6-v2's native dimension: makes pre-calibration raw
    # truncation a no-op so these tests measure real semantic similarity.
    # Threshold lowered per General-spec.txt:585-592 ("a parameter, not a
    # fixed truth") — see the identical comment in test_fingerprint_engine.py.
    config = FingerprintConfig(
        embedding_model=TEST_MODEL, compressed_dimensions=384,
        similarity_threshold_near_duplicate=0.75,
    )
    engine = FingerprintEngine(ledger, config)
    return SeedManager(ledger, engine)


def test_first_seed_submission_is_novel(manager):
    result = manager.submit(MATH_SEED_A, domain="mathematics")
    assert result.ok
    assert result.duplicate_classification == "NOVEL"
    assert result.feedback is None


def test_paraphrased_seed_is_flagged_not_rejected(manager):
    first = manager.submit(MATH_SEED_A, domain="mathematics")
    second = manager.submit(MATH_SEED_A_PARAPHRASE, domain="mathematics")

    assert second.ok  # never auto-rejected
    assert second.duplicate_classification in ("NEAR_DUPLICATE", "EXACT_DUPLICATE")
    assert second.duplicate_of_seed_id == first.seed_id
    assert "flagged, not rejected" in second.feedback


def test_distinct_seed_is_not_flagged(manager):
    manager.submit(MATH_SEED_A, domain="mathematics")
    result = manager.submit(MATH_SEED_B, domain="mathematics")
    assert result.duplicate_classification == "NOVEL"
    assert result.feedback is None


def test_cross_domain_conceptual_duplicate_is_still_flagged(manager):
    # Same underlying structural idea, submitted under a different domain tag.
    manager.submit(MATH_SEED_A, domain="mathematics")
    result = manager.submit(MATH_SEED_A_PARAPHRASE, domain="cross-domain")
    assert result.duplicate_classification in ("NEAR_DUPLICATE", "EXACT_DUPLICATE")
