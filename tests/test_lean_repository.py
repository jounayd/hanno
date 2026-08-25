"""LeanVerification ledger methods -- Step 6 pilot: writing/reading the
previously-unused lean_verifications table, and the query that finds
mathematics candidates still needing a Lean check."""
import pytest

from hanno.ledger.models import Candidate, LeanVerification, Seed
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


def _candidate(seed_id, **overrides):
    defaults = dict(
        seed_id=seed_id, domain="mathematics", temperature=0.5, prompt_variant="direct_conjecture",
        model="test-model", reasoning_effort="standard", raw_text="{}", core_claim="a claim",
        formal_statement="for all x, x = x", parse_status="ok", prompt_text="prompt",
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def test_insert_and_list_lean_verification_by_candidate(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)

    verification = LeanVerification(
        candidate_id=c.id, lean_code="theorem foo : True := by sorry",
        translation_attempt_number=1, lean_stdout="", lean_stderr="",
        result="KNOWN", execution_time_ms=120,
    )
    ledger.insert_lean_verification(verification)

    rows = ledger.list_lean_verifications_by_candidate(c.id)
    assert len(rows) == 1
    assert rows[0]["result"] == "KNOWN"
    assert rows[0]["lean_code"] == "theorem foo : True := by sorry"
    assert rows[0]["execution_time_ms"] == 120


def test_list_mathematics_candidates_without_lean_check_excludes_checked(ledger, seed):
    checked = _candidate(seed.id)
    unchecked = _candidate(seed.id)
    ledger.insert_candidate(checked)
    ledger.insert_candidate(unchecked)
    ledger.insert_lean_verification(LeanVerification(candidate_id=checked.id, result="KNOWN"))

    pending = ledger.list_mathematics_candidates_without_lean_check(limit=10)

    ids = [c["id"] for c in pending]
    assert unchecked.id in ids
    assert checked.id not in ids


def test_list_mathematics_candidates_without_lean_check_excludes_other_domains_and_failed_parses(ledger, seed):
    other_domain = _candidate(seed.id, domain="philosophy")
    failed_parse = _candidate(seed.id, parse_status="failed")
    no_formal_statement = _candidate(seed.id, formal_statement=None)
    eligible = _candidate(seed.id)
    for c in (other_domain, failed_parse, no_formal_statement, eligible):
        ledger.insert_candidate(c)

    pending = ledger.list_mathematics_candidates_without_lean_check(limit=10)

    ids = [c["id"] for c in pending]
    assert ids == [eligible.id]


def test_list_mathematics_candidates_without_lean_check_respects_limit(ledger, seed):
    for _ in range(5):
        ledger.insert_candidate(_candidate(seed.id))

    pending = ledger.list_mathematics_candidates_without_lean_check(limit=2)

    assert len(pending) == 2
