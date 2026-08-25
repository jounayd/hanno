"""Human reseed: submitting a new seed derived from a specific mind-map
candidate, tracked via the append-only reseed_events ledger table with
correctly resolved chain_depth/original_seed_id across multi-hop chains."""
import pytest

from hanno.config import FingerprintConfig
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Candidate
from hanno.ledger.repository import Ledger
from hanno.seeds.manager import SeedManager

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

SEED_A = (
    "Investigate whether every finite simple group embeds into a symmetric "
    "group of bounded degree, connecting to representation theory and algebra."
)
RESEED_TEXT_1 = (
    "Restrict the embedding question to alternating groups specifically, "
    "connecting to representation theory and algebra."
)
RESEED_TEXT_2 = (
    "Restrict the alternating-group embedding question further to degree-6 "
    "cases only, connecting to representation theory and algebra."
)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


@pytest.fixture
def manager(ledger):
    config = FingerprintConfig(
        embedding_model=TEST_MODEL, compressed_dimensions=384,
        similarity_threshold_near_duplicate=0.75,
    )
    engine = FingerprintEngine(ledger, config)
    return SeedManager(ledger, engine)


def make_candidate(ledger, seed_id, **overrides):
    fields = dict(
        seed_id=seed_id, domain="mathematics", temperature=0.7,
        prompt_variant="baseline", model="claude-test",
        prompt_text="p", raw_text="r", parse_status="ok",
        core_claim="claim", formal_statement="statement",
        suggested_seeds=["a follow-up seed", "another follow-up seed"],
    )
    fields.update(overrides)
    c = Candidate(**fields)
    ledger.insert_candidate(c)
    return c


def test_reseed_from_candidate_of_an_original_seed_has_chain_depth_one(manager, ledger):
    submit = manager.submit(SEED_A, domain="mathematics")
    candidate = make_candidate(ledger, submit.seed_id)

    result = manager.reseed_from_candidate(candidate.id, RESEED_TEXT_1, domain="mathematics")

    assert result.ok
    assert result.seed_id is not None
    assert result.seed_id != submit.seed_id
    assert result.original_seed_id == submit.seed_id
    assert result.chain_depth == 1


def test_reseed_event_is_recorded_with_expected_fields(manager, ledger):
    submit = manager.submit(SEED_A, domain="mathematics")
    candidate = make_candidate(ledger, submit.seed_id)

    result = manager.reseed_from_candidate(
        candidate.id, RESEED_TEXT_1, domain="mathematics", operator_id="jounayd",
    )

    events = ledger.list_reseed_events_by_seed(submit.seed_id)
    assert len(events) == 1
    event = events[0]
    assert event["original_seed_id"] == submit.seed_id
    assert event["parent_seed_id"] == submit.seed_id
    assert event["generated_reseed_id"] == result.seed_id
    assert event["trigger_type"] == "manual"
    assert event["chain_depth"] == 1
    assert event["operator_id"] == "jounayd"


def test_second_hop_reseed_inherits_original_seed_and_increments_depth(manager, ledger):
    submit = manager.submit(SEED_A, domain="mathematics")
    candidate_1 = make_candidate(ledger, submit.seed_id)

    first_reseed = manager.reseed_from_candidate(candidate_1.id, RESEED_TEXT_1, domain="mathematics")
    assert first_reseed.chain_depth == 1

    # A second reseed off a candidate mined FROM the first reseed's new seed.
    candidate_2 = make_candidate(ledger, first_reseed.seed_id)
    second_reseed = manager.reseed_from_candidate(candidate_2.id, RESEED_TEXT_2, domain="mathematics")

    assert second_reseed.ok
    assert second_reseed.original_seed_id == submit.seed_id  # still the root, not first_reseed.seed_id
    assert second_reseed.chain_depth == 2

    events = ledger.list_reseed_events_by_seed(first_reseed.seed_id)
    assert len(events) == 2  # this seed is parent of one event, generated_reseed of the other


def test_reseed_from_unknown_candidate_fails_without_side_effects(manager, ledger):
    result = manager.reseed_from_candidate("does-not-exist", RESEED_TEXT_1, domain="mathematics")

    assert not result.ok
    assert result.seed_id is None
    assert ledger.list_seeds() == []


def test_reseed_event_records_triggering_candidate_id(manager, ledger):
    submit = manager.submit(SEED_A, domain="mathematics")
    candidate = make_candidate(ledger, submit.seed_id)

    manager.reseed_from_candidate(candidate.id, RESEED_TEXT_1, domain="mathematics")

    event = ledger.get_reseed_event_by_generated_seed(
        ledger.list_reseed_events_by_seed(submit.seed_id)[0]["generated_reseed_id"]
    )
    assert event["triggering_candidates"] == f'["{candidate.id}"]'
