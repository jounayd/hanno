"""solve_claim (EXCAVATOR_MIGRATION_PLAN.md Phase 1): prove or disprove
exactly one given claim, with no generation/exploration/self-rating step.
Hermetic -- autoformalize_with_retry, search_for_counterexample, and
prove_with_retry are all faked at the hanno.solve.pipeline import site, so
this suite never needs a real API key, network call, or Lean toolchain."""

from unittest.mock import patch

import pytest

from hanno.lean.autoformalize import AutoformalizeWithCheckResult
from hanno.lean.checker import CheckResult
from hanno.lean.prove import ProofResult
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import SkillResult
from hanno.solve import pipeline as solve_module
from hanno.solve.pipeline import SOLVE_PROMPT_VARIANT, solve_claim
from hanno.verification.counterexample import CounterexampleResult

CLAIM = "For every integer n, n^11 - n is divisible by 66."


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "test.db")
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def seed(ledger):
    s = Seed(text=CLAIM, domain="mathematics", type="human")
    ledger.insert_seed(s)
    return s


def _skill():
    return SkillDefinition(
        name="run_code",
        description="test",
        command=["python3", "run_code.py"],
        input_mode="stdin",
        timeout_seconds=25,
        max_output_chars=6000,
        enabled=True,
    )


def _survivor_outcome(
    lean_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by sorry",
):
    return AutoformalizeWithCheckResult(
        lean_code=lean_code,
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=100),
        attempts=1,
        input_tokens=100,
        output_tokens=50,
    )


def _untranslatable_outcome():
    return AutoformalizeWithCheckResult(lean_code=None, check=None, attempts=3)


def _malformed_outcome():
    return AutoformalizeWithCheckResult(
        lean_code="import Mathlib\n\ntheorem foo : Bogus := by sorry",
        check=CheckResult(
            compiles=False, trivial_by=None, stdout="error", stderr="", duration_ms=100
        ),
        attempts=3,
    )


def _known_outcome():
    return AutoformalizeWithCheckResult(
        lean_code="import Mathlib\n\ntheorem foo : True := by sorry",
        check=CheckResult(
            compiles=True, trivial_by="exact?", stdout="", stderr="", duration_ms=100
        ),
        attempts=1,
    )


class FakeClient:
    async def close(self):
        pass

    async def generate(self, *args, **kwargs):
        raise AssertionError("Unexpected model call in orchestration test")


@pytest.mark.asyncio
async def test_untranslatable_claim_stops_before_any_discovery(ledger, seed, tmp_path):
    with patch.object(
        solve_module, "autoformalize_with_retry", return_value=_untranslatable_outcome()
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
        )

    assert result.stage_reached == "untranslatable"
    assert result.discovery_id is None
    assert ledger.list_discoveries() == []
    verifications = ledger.list_lean_verifications_by_candidate(result.candidate_id)
    assert len(verifications) == 1
    assert verifications[0]["result"] == "UNTRANSLATABLE"


@pytest.mark.asyncio
async def test_malformed_claim_stops_before_any_discovery(ledger, seed, tmp_path):
    with patch.object(solve_module, "autoformalize_with_retry", return_value=_malformed_outcome()):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
        )

    assert result.stage_reached == "malformed"
    assert result.discovery_id is None
    assert ledger.list_discoveries() == []


@pytest.mark.asyncio
async def test_trivially_known_claim_stops_before_any_discovery(ledger, seed, tmp_path):
    with patch.object(solve_module, "autoformalize_with_retry", return_value=_known_outcome()):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
        )

    assert result.stage_reached == "trivial"
    assert result.discovery_id is None
    assert ledger.list_discoveries() == []


@pytest.mark.asyncio
async def test_survivor_without_skill_skips_counterexample_and_proves(ledger, seed, tmp_path):
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
        input_tokens=200,
        output_tokens=300,
    )
    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "prove_with_retry", return_value=proved) as mock_prove,
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            run_code_skill=None,
        )

    mock_prove.assert_called_once()
    assert result.stage_reached == "proved"
    assert result.proved is True
    assert result.discovery_id is not None
    # Phase 0 instrumentation: total is summed across the formalize leg
    # (100/50 from _survivor_outcome) and the prove leg (200/300) -- no
    # counterexample leg here since run_code_skill=None skips it.
    assert result.input_tokens == 300
    assert result.output_tokens == 350
    discoveries = ledger.list_discoveries()
    assert len(discoveries) == 1
    assert discoveries[0]["candidate_id"] == result.candidate_id


@pytest.mark.asyncio
async def test_refuted_by_counterexample_never_calls_prove(ledger, seed, tmp_path):
    ce_result = CounterexampleResult(
        refuted=True,
        program="for n in range(1000): pass",
        skill_result=SkillResult(
            stdout="COUNTEREXAMPLE FOUND: n=5",
            stderr="",
            exit_code=0,
            duration_ms=100,
        ),
    )
    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "search_for_counterexample", return_value=ce_result),
        patch.object(solve_module, "prove_with_retry") as mock_prove,
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            run_code_skill=_skill(),
        )

    mock_prove.assert_not_called()
    assert result.stage_reached == "refuted"
    assert result.refuted is True
    assert result.discovery_id is not None  # still recorded, just refuted afterward
    invocations = ledger.list_tool_invocations_by_candidate(result.candidate_id)
    assert len(invocations) == 1
    assert invocations[0]["skill_name"] == "run_code"


@pytest.mark.asyncio
async def test_unresolved_when_proof_search_exhausts(ledger, seed, tmp_path):
    ce_result = CounterexampleResult(
        refuted=False,
        program="for n in range(1000): pass",
        skill_result=SkillResult(
            stdout="NO COUNTEREXAMPLE FOUND after testing 1000 cases",
            stderr="",
            exit_code=0,
            duration_ms=100,
        ),
    )
    unresolved = ProofResult(proved=False, proof_code="partial attempt", check=None, attempts=5)
    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "search_for_counterexample", return_value=ce_result),
        patch.object(solve_module, "prove_with_retry", return_value=unresolved),
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            run_code_skill=_skill(),
        )

    assert result.stage_reached == "unresolved"
    assert result.proved is False
    assert result.discovery_id is not None


def _mathlib_search_skill():
    return SkillDefinition(
        name="mathlib_search",
        description="test",
        command=["python3", "mathlib_search.py"],
        input_mode="stdin",
        timeout_seconds=20,
        max_output_chars=4000,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_mathlib_search_skill_is_offered_to_formalize_and_prove(ledger, seed, tmp_path):
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
    )
    with (
        patch.object(
            solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()
        ) as mock_af,
        patch.object(solve_module, "prove_with_retry", return_value=proved) as mock_prove,
    ):
        await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            mathlib_search_skill=_mathlib_search_skill(),
        )

    af_kwargs = mock_af.call_args.kwargs
    assert af_kwargs["tools"][0].name == "mathlib_search"
    assert callable(af_kwargs["tool_executor"])
    prove_kwargs = mock_prove.call_args.kwargs
    assert prove_kwargs["tools"][0].name == "mathlib_search"
    assert callable(prove_kwargs["tool_executor"])


@pytest.mark.asyncio
async def test_mathlib_search_invocations_during_prove_are_recorded_in_the_ledger(
    ledger, seed, tmp_path, monkeypatch
):
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
    )

    async def prove_side_effect(*args, tools=None, tool_executor=None, **kwargs):
        assert tool_executor is not None
        tool_executor("mathlib_search", {"input": "Nat.dvd_of_dvd_of_dvd_mul_left"})
        return proved

    fake_skill_result = SkillResult(stdout="NOT FOUND", stderr="", exit_code=0, duration_ms=50)
    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "prove_with_retry", side_effect=prove_side_effect),
        patch.object(solve_module, "run_skill", return_value=fake_skill_result),
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            mathlib_search_skill=_mathlib_search_skill(),
        )

    invocations = ledger.list_tool_invocations_by_candidate(result.candidate_id)
    assert len(invocations) == 1
    assert invocations[0]["skill_name"] == "mathlib_search"
    assert invocations[0]["input_text"] == "Nat.dvd_of_dvd_of_dvd_mul_left"


def _lean_goal_solver_skill():
    return SkillDefinition(
        name="lean_goal_solver",
        description="test",
        command=["python3", "lean_goal_solver.py"],
        input_mode="stdin",
        timeout_seconds=200,
        max_output_chars=2000,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_both_grounding_skills_are_offered_together(ledger, seed, tmp_path):
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
    )
    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "prove_with_retry", return_value=proved) as mock_prove,
    ):
        await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            mathlib_search_skill=_mathlib_search_skill(),
            lean_goal_solver_skill=_lean_goal_solver_skill(),
        )

    prove_tools = mock_prove.call_args.kwargs["tools"]
    assert {t.name for t in prove_tools} == {"mathlib_search", "lean_goal_solver"}


@pytest.mark.asyncio
async def test_grounding_executor_dispatches_to_the_correct_skill_by_name(ledger, seed, tmp_path):
    # With both tools offered, a call named "lean_goal_solver" must run
    # THAT skill, not silently fall back to mathlib_search (a real risk
    # once the executor has more than one skill to choose between).
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
    )

    async def prove_side_effect(*args, tools=None, tool_executor=None, **kwargs):
        assert tool_executor is not None
        tool_executor("lean_goal_solver", {"input": "theorem goal (n : ℕ) : n + 1 > n"})
        return proved

    run_skill_calls = []

    def fake_run_skill(skill, input_text):
        run_skill_calls.append(skill.name)
        return SkillResult(
            stdout="WORKS -- `norm_num` closes this goal.", stderr="", exit_code=0, duration_ms=50
        )

    with (
        patch.object(solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()),
        patch.object(solve_module, "prove_with_retry", side_effect=prove_side_effect),
        patch.object(solve_module, "run_skill", side_effect=fake_run_skill),
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            mathlib_search_skill=_mathlib_search_skill(),
            lean_goal_solver_skill=_lean_goal_solver_skill(),
        )

    assert run_skill_calls == ["lean_goal_solver"]
    invocations = ledger.list_tool_invocations_by_candidate(result.candidate_id)
    assert [i["skill_name"] for i in invocations] == ["lean_goal_solver"]


@pytest.mark.asyncio
async def test_prover_client_is_used_for_proving_not_the_main_client(ledger, seed, tmp_path):
    proved = ProofResult(
        proved=True,
        proof_code="import Mathlib\n\ntheorem foo (n : ℕ) : 66 ∣ (n ^ 11 - n) := by decide",
        check=CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=200),
        attempts=1,
    )
    main_client = FakeClient()
    prover_client = FakeClient()
    with (
        patch.object(
            solve_module, "autoformalize_with_retry", return_value=_survivor_outcome()
        ) as mock_af,
        patch.object(solve_module, "prove_with_retry", return_value=proved) as mock_prove,
    ):
        await solve_claim(
            CLAIM,
            seed.id,
            main_client,
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
            prover_client=prover_client,
        )

    assert mock_af.call_args.args[1] is main_client  # formalize always uses the main client
    assert mock_prove.call_args.args[1] is prover_client  # prove uses the dedicated one


@pytest.mark.asyncio
async def test_candidate_row_carries_the_exact_claim_unmodified(ledger, seed, tmp_path):
    with patch.object(
        solve_module, "autoformalize_with_retry", return_value=_untranslatable_outcome()
    ):
        result = await solve_claim(
            CLAIM,
            seed.id,
            FakeClient(),
            tmp_path,
            ledger,
            lean_timeout_s=10,
            max_translation_attempts=3,
            max_proof_attempts=5,
        )

    candidate = ledger.get_candidate(result.candidate_id)
    assert candidate["core_claim"] == CLAIM
    assert candidate["formal_statement"] == CLAIM
    assert candidate["prompt_variant"] == SOLVE_PROMPT_VARIANT
    assert candidate["seed_id"] == seed.id
