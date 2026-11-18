"""Pipeline <-> skills integration: when skills are registered, the client
receives tool definitions and an executor; every tool invocation the model
makes is persisted to the append-only ledger, linked to the candidate that
made it. With no skills registered, behavior is byte-identical to before
(covered implicitly by every other pipeline test)."""

import json
import subprocess
import sys

import pytest

from hanno.config import FingerprintConfig, HannoConfig
from hanno.excavator.client import GenerationResult
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition

TEST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
PY = sys.executable


def _payload(core_claim):
    return json.dumps(
        {
            "domain": "mathematics",
            "core_claim": core_claim,
            "formal_statement": "forall x, P(x)",
            "intuition": "intuition text",
            "connections": ["algebra"],
            "self_assessed_novelty": "n/a",
            "identified_weaknesses": "n/a",
            "exploration_signal": "continue",
            "suggested_seeds": [],
            "self_rated_novelty": 0.7,
            "self_rated_rigor": 0.7,
            "claim_type": "structural",
        }
    )


class ToolUsingFakeClient:
    """Simulates a model that calls the checker tool once, then answers."""

    def __init__(self):
        self.received_tools = None
        self.received_cache_prefix = None

    async def generate(
        self,
        prompt,
        temperature,
        max_tokens=None,
        thinking_budget_tokens=None,
        tools=None,
        tool_executor=None,
        max_tool_rounds=3,
        cache_prefix=None,
        **kwargs,
    ):
        self.received_tools = tools
        self.received_cache_prefix = cache_prefix
        if tools and tool_executor:
            assert tool_executor is not None
            tool_executor("upper_tool", {"input": "check this claim"})
        return GenerationResult(
            text=_payload("A tested claim about additive structures in trees."),
            input_tokens=10,
            output_tokens=10,
            tool_rounds=1 if tools else 0,
        )

    async def close(self):
        pass


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "test.db")
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def fingerprint_engine(ledger):
    config = FingerprintConfig(embedding_model=TEST_MODEL, compressed_dimensions=384)
    return FingerprintEngine(ledger, config)


@pytest.mark.asyncio
async def test_tool_invocations_are_persisted_and_linked(ledger, fingerprint_engine):
    skill = SkillDefinition(
        name="upper_tool",
        description="Uppercases input.",
        command=[PY, "-c", "import sys; print(sys.stdin.read().upper())"],
    )
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    seed = Seed(
        text="a seed about graph theory and tree labelings", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)

    client = ToolUsingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client, skills=[skill])
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    assert client.received_tools is not None
    assert client.received_tools[0].name == "upper_tool"

    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert "verification tools available" in candidate["prompt_text"]
    assert "upper_tool" in candidate["prompt_text"]

    invocations = ledger.list_tool_invocations_by_candidate(candidate["id"])
    assert len(invocations) == 1
    inv = invocations[0]
    assert inv["skill_name"] == "upper_tool"
    assert inv["input_text"] == "check this claim"
    assert "CHECK THIS CLAIM" in inv["stdout"]
    assert inv["exit_code"] == 0


@pytest.mark.asyncio
async def test_no_skills_means_no_tools_passed(ledger, fingerprint_engine):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    seed = Seed(
        text="a seed about graph theory and tree labelings", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)

    client = ToolUsingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client, skills=None)
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    assert client.received_tools is None
    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert "verification tools available" not in candidate["prompt_text"]
    assert ledger.list_tool_invocations_by_candidate(candidate["id"]) == []


@pytest.mark.asyncio
async def test_generation_call_receives_the_stable_prompt_prefix_for_caching(
    ledger, fingerprint_engine
):
    # Phase 2 (EXCAVATOR_MIGRATION_PLAN.md): the per-variant instructions in
    # hanno/excavator/prompts.py are byte-identical across every candidate --
    # the pipeline must pass that exact substring as cache_prefix so
    # AnthropicClient can mark it as an ephemeral cache breakpoint.
    from hanno.excavator.prompts import ALL_VARIANTS

    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 1000.0
    seed = Seed(
        text="a seed about graph theory and tree labelings", domain="mathematics", type="human"
    )
    ledger.insert_seed(seed)

    client = ToolUsingFakeClient()
    pipeline = ExcavatorPipeline(ledger, config, fingerprint_engine, client=client, skills=None)
    await pipeline.run_cycle(seed_id=seed.id, seed_text=seed.text, n_candidates=1)

    assert client.received_cache_prefix in ALL_VARIANTS.values()
    candidate = ledger.list_candidates_by_seed(seed.id)[0]
    assert candidate["prompt_text"].startswith(client.received_cache_prefix)


def test_graceful_check_script_accepts_a_valid_labeling():
    # Path P4 labeled 0-3-1-2: edge labels |0-3|=3, |3-1|=2, |1-2|=1 -> graceful
    payload = json.dumps(
        {
            "edges": [["a", "b"], ["b", "c"], ["c", "d"]],
            "labels": {"a": 0, "b": 3, "c": 1, "d": 2},
        }
    )
    proc = subprocess.run(
        [PY, "skills/scripts/graceful_check.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    verdict = json.loads(proc.stdout)
    assert verdict["graceful"] is True
    assert verdict["edge_labels"] == [1, 2, 3]


def test_graceful_check_script_rejects_a_bad_labeling():
    # Star K1,3 labeled center=1: edge labels 1,1,2 -> duplicate, not graceful
    payload = json.dumps(
        {
            "edges": [["c", "x"], ["c", "y"], ["c", "z"]],
            "labels": {"c": 1, "x": 0, "y": 2, "z": 3},
        }
    )
    proc = subprocess.run(
        [PY, "skills/scripts/graceful_check.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    verdict = json.loads(proc.stdout)
    assert verdict["graceful"] is False
    assert verdict["edge_labels_valid"] is False
