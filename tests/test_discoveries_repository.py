"""Discovery ledger methods -- writing/reading the previously-unused
discoveries table, populated when run_iterative_mining(until_promising=True)
finds a candidate that clears the Lean triviality filter."""
import pytest

from hanno.ledger.models import Candidate, Discovery, Seed
from hanno.ledger.repository import Ledger


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


@pytest.fixture
def seed(ledger):
    s = Seed(text="a seed about sparse rulers", domain="mathematics", type="human")
    ledger.insert_seed(s)
    return s


@pytest.fixture
def candidate(ledger, seed):
    c = Candidate(
        seed_id=seed.id, domain="mathematics", temperature=0.5, prompt_variant="direct_conjecture",
        model="test-model", reasoning_effort="standard", raw_text="{}", core_claim="a claim",
        formal_statement="for all x, x = x", parse_status="ok", prompt_text="prompt",
    )
    ledger.insert_candidate(c)
    return c


def test_insert_and_list_discovery_round_trip(ledger, seed, candidate):
    discovery = Discovery(
        candidate_id=candidate.id, seed_id=seed.id, domain="mathematics",
        statement=candidate.core_claim, formal_statement="theorem foo : True := by sorry",
        verification_result="SURVIVED", lean_verified=True, human_reviewed=False,
    )
    ledger.insert_discovery(discovery)

    rows = ledger.list_discoveries()

    assert len(rows) == 1
    assert rows[0]["candidate_id"] == candidate.id
    assert rows[0]["seed_id"] == seed.id
    assert rows[0]["verification_result"] == "SURVIVED"
    assert rows[0]["lean_verified"] == 1
    assert rows[0]["human_reviewed"] == 0
    assert rows[0]["human_verdict"] is None


def test_discoveries_default_unreviewed(ledger, seed, candidate):
    ledger.insert_discovery(Discovery(candidate_id=candidate.id, seed_id=seed.id, domain="mathematics"))

    rows = ledger.list_discoveries()

    assert rows[0]["human_reviewed"] == 0
    assert rows[0]["lean_verified"] is None
