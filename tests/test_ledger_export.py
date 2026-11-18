"""hanno.ledger.export.build_trace_export: the shared JSON payload used by
both `hanno trace --json` and the UI's /api/seeds/{id}/trace endpoint --
must stay a stable, documented shape for hanno/ui/static/mindmap.js."""

import pytest

from hanno.ledger.export import MINDMAP_CANDIDATE_FIELDS, build_trace_export
from hanno.ledger.models import Candidate, Discovery, LeanVerification, Seed, ToolInvocation
from hanno.ledger.repository import Ledger


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "test.db")
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def seed(ledger):
    s = Seed(text="a seed about sparse rulers", domain="mathematics", type="human")
    ledger.insert_seed(s)
    return s


def _candidate(seed_id, **overrides):
    defaults: dict = {
        "seed_id": seed_id,
        "domain": "mathematics",
        "temperature": 0.5,
        "prompt_variant": "direct_conjecture",
        "model": "test-model",
        "reasoning_effort": "standard",
        "raw_text": "{}",
        "core_claim": "a claim",
        "formal_statement": "forall x",
        "intuition": "because",
        "connections": ["algebra"],
        "self_assessed_novelty": "n/a",
        "identified_weaknesses": "n/a",
        "parse_status": "ok",
        "duplicate_status": "NOVEL",
        "exploration_signal": "continue",
        "claim_type": "structural",
        "numeric_check": {"assignments": {"x": 1}, "equation": "x == 1"},
        "numeric_check_result": True,
        "path_score": 0.5,
        "self_rated_novelty": 0.5,
        "self_rated_rigor": 0.5,
        "prompt_text": "prompt",
        "input_tokens": 10,
        "output_tokens": 20,
    }
    defaults.update(overrides)
    return Candidate(**defaults)


def test_export_shape_matches_allowlist(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)

    assert set(payload.keys()) == {"seed", "candidates"}
    assert payload["seed"]["id"] == seed.id
    assert payload["seed"]["status"] == "queued"
    entry = payload["candidates"][0]
    for field in MINDMAP_CANDIDATE_FIELDS:
        assert field in entry
    assert entry["tool_invocations"] == []
    assert entry["lean_verifications"] == []
    assert entry["discovery"] is None


def test_json_string_fields_are_parsed_to_real_json(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)
    entry = payload["candidates"][0]

    assert entry["connections"] == ["algebra"]
    assert entry["numeric_check"] == {"assignments": {"x": 1}, "equation": "x == 1"}
    assert entry["numeric_check_result"] is True  # real JSON bool, not sqlite's 0/1


def test_numeric_check_result_none_stays_none(ledger, seed):
    c = _candidate(seed.id, numeric_check=None, numeric_check_result=None)
    ledger.insert_candidate(c)
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)
    entry = payload["candidates"][0]

    assert entry["numeric_check"] is None
    assert entry["numeric_check_result"] is None


def test_tool_invocations_are_included_per_candidate(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    ledger.insert_tool_invocation(
        ToolInvocation(
            candidate_id=c.id,
            skill_name="run_code",
            input_text="print(1)",
            stdout="1",
            stderr="",
            exit_code=0,
            duration_ms=12,
            timed_out=False,
        )
    )
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)
    invocations = payload["candidates"][0]["tool_invocations"]

    assert len(invocations) == 1
    assert invocations[0]["skill_name"] == "run_code"
    assert invocations[0]["timed_out"] is False


def test_lean_verifications_are_included_per_candidate(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    ledger.insert_lean_verification(
        LeanVerification(
            candidate_id=c.id,
            lean_code="theorem foo : True := by sorry",
            translation_attempt_number=2,
            lean_stdout="",
            lean_stderr="",
            result=None,
            execution_time_ms=500,
        )
    )
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)
    verifications = payload["candidates"][0]["lean_verifications"]

    assert len(verifications) == 1
    assert verifications[0]["lean_code"] == "theorem foo : True := by sorry"
    assert verifications[0]["translation_attempt_number"] == 2
    assert verifications[0]["result"] is None  # a survivor -- never trivially closed


def test_discovery_is_attached_to_its_candidate(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    ledger.insert_discovery(
        Discovery(
            candidate_id=c.id,
            seed_id=seed.id,
            domain="mathematics",
            statement="a claim",
            formal_statement="theorem foo : True := by sorry",
            verification_result="SURVIVED",
            lean_verified=True,
            human_reviewed=False,
        )
    )
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)
    discovery = payload["candidates"][0]["discovery"]

    assert discovery is not None
    assert discovery["verification_result"] == "SURVIVED"
    assert discovery["lean_verified"] is True
    assert discovery["human_reviewed"] is False


def test_candidate_without_a_discovery_gets_none(ledger, seed):
    c = _candidate(seed.id)
    ledger.insert_candidate(c)
    candidates = ledger.list_candidates_by_seed(seed.id)

    payload = build_trace_export(dict(ledger.get_seed(seed.id)), "queued", candidates, ledger)

    assert payload["candidates"][0]["discovery"] is None
