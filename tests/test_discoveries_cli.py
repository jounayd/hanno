"""Deep-verification state must not be confused with generation-time tools."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hanno.cli.commands import discoveries
from hanno.config import HannoConfig
from hanno.lean.prove import ProofResult
from hanno.ledger.models import Candidate, Discovery, LeanVerification, Seed, ToolInvocation
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import SkillResult
from hanno.verification.counterexample import CounterexampleResult


@pytest.fixture
def discovery_context(tmp_path):
    ledger = Ledger.connect(tmp_path / "ledger.db")
    ledger.init_schema()
    seed = Seed(
        text="Explore a theorem about integer divisibility and bounded sums.", domain="mathematics"
    )
    ledger.insert_seed(seed)
    candidate = Candidate(
        seed_id=seed.id,
        domain="mathematics",
        temperature=0,
        prompt_variant="test",
        model="fixture",
        prompt_text="",
        raw_text="",
        parse_status="ok",
    )
    ledger.insert_candidate(candidate)
    discovery = Discovery(
        candidate_id=candidate.id,
        seed_id=seed.id,
        domain="mathematics",
        statement=seed.text,
        formal_statement="import Mathlib\ntheorem target : True := by sorry",
    )
    ledger.insert_discovery(discovery)
    ledger.insert_lean_verification(LeanVerification(candidate_id=candidate.id))
    ledger.insert_tool_invocation(
        ToolInvocation(
            candidate_id=candidate.id,
            skill_name="run_code",
            input_text="",
            stdout="generation-time evidence",
            stderr="",
            exit_code=0,
            duration_ms=1,
        )
    )
    config = HannoConfig()
    config.lean_verifier.mathlib_cache_path = str(tmp_path)
    yield SimpleNamespace(config=config, ledger=ledger), ledger.list_discoveries()[0]
    ledger.close()


def test_tool_assisted_discovery_is_verified_and_worker_results_are_persisted(discovery_context):
    context, discovery = discovery_context
    skills = [
        SkillDefinition(name=name, description="fixture", command=["unused"])
        for name in ("run_code", "mathlib_search")
    ]
    ce = CounterexampleResult(
        False, "print('checked')", SkillResult("NO COUNTEREXAMPLE FOUND", "", 0, 1)
    )
    output = []

    async def prove(*args, tool_executor=None, **kwargs):
        assert tool_executor is not None
        output.append(
            await asyncio.to_thread(tool_executor, "mathlib_search", {"input": "Nat.add_comm"})
        )
        return ProofResult(False, None, None, 1)

    async def close():
        pass

    with (
        patch.object(discoveries, "build_context", return_value=context),
        patch.object(discoveries, "load_skill_files", return_value=skills),
        patch.object(discoveries, "build_client", return_value=SimpleNamespace(close=close)),
        patch.object(discoveries, "build_prover_client", return_value=None),
        patch.object(discoveries, "search_for_counterexample", return_value=ce) as search,
        patch.object(discoveries, "prove_with_retry", side_effect=prove),
        patch.object(
            discoveries, "run_skill", return_value=SkillResult("EXISTS Nat.add_comm", "", 0, 1)
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(discoveries.app, ["verify"])
        assert result.exit_code == 0, result.output
        search.assert_called_once()
        assert "EXISTS Nat.add_comm" in output[0]
        invocations = context.ledger.list_tool_invocations_by_candidate(discovery["candidate_id"])
        assert [inv["phase"] for inv in invocations] == ["generation", "counterexample", "proof"]
        listed = runner.invoke(discoveries.app, ["list"])
        assert listed.exit_code == 0, listed.output
        assert "unresolved" in listed.output
        again = runner.invoke(discoveries.app, ["verify"])
        assert again.exit_code == 0
        assert "Nothing to verify" in again.output
        search.assert_called_once()


def test_generation_counterexample_marker_does_not_mark_deep_verification_complete(
    discovery_context,
):
    context, discovery = discovery_context
    context.ledger.insert_tool_invocation(
        ToolInvocation(
            candidate_id=discovery["candidate_id"],
            skill_name="run_code",
            input_text="",
            stdout="COUNTEREXAMPLE FOUND: during generation",
            stderr="",
            exit_code=0,
            duration_ms=1,
        )
    )
    assert discoveries._status(context.ledger, discovery) == "not yet verified"
