"""SOLVE mode (EXCAVATOR_MIGRATION_PLAN.md Phase 1): prove or disprove
EXACTLY one given claim. No candidate generation, no "be non-obvious"
instruction, no self-rated scoring -- the direct fix for the goal-drift
failure mode observed in EXPLORE-mode mining, where every candidate on a
seed asking to prove "n^11 - n is divisible by 66" abandoned the concrete
claim for progressively more abstract, unrelated reformulations (a
maximality theorem, an algorithmic-complexity claim, a fabricated
"Frobenius-Invariant Divisor" framework -- never the actual question).

Reuses the existing formalize / counterexample / prove legs verbatim --
autoformalize_with_retry, search_for_counterexample, prove_with_retry are
untouched, and carry today's strength-preservation rules (no vacuous
existentials, no backwards relations) automatically. What's new here is the
entry point: the claim is fixed by the caller and never regenerated or
reformulated by the model at any stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hanno.excavator.client import ModelClient
from hanno.lean.autoformalize import autoformalize_with_retry
from hanno.lean.checker import classify_result
from hanno.lean.prove import prove_with_retry
from hanno.ledger.models import Candidate, Discovery, LeanVerification, ToolInvocation
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import format_for_model, run_skill
from hanno.verification.counterexample import search_for_counterexample

# Distinguishes solve-mode candidates from explore-mode ones in the ledger
# (e.g. for the benchmark runner and the UI) without any schema change --
# a solve-mode "candidate" is the claim itself, not a generated proposal.
SOLVE_PROMPT_VARIANT = "solve_mode_direct"


@dataclass
class SolveResult:
    candidate_id: str
    triviality_lean_code: str | None
    triviality_result: (
        str | None
    )  # classify_result(...): None means "survived" (compiles, non-trivial)
    refuted: bool
    counterexample_output: str | None
    proved: bool
    proof_code: str | None
    discovery_id: str | None
    # "untranslatable" | "malformed" | "timeout" | "trivial" | "refuted" | "unresolved" | "proved"
    stage_reached: str
    # Phase 0 instrumentation: summed across every leg actually run
    # (formalize + counterexample + prove), not just one of them -- the
    # basis for real solve-mode cost accounting (see hanno/solve/benchmark.py).
    input_tokens: int = 0
    output_tokens: int = 0


async def solve_claim(
    claim: str,
    seed_id: str,
    client: ModelClient,
    project_dir: Path,
    ledger: Ledger,
    lean_timeout_s: int,
    max_translation_attempts: int,
    max_proof_attempts: int,
    model_name: str = "solve-mode",
    run_code_skill: SkillDefinition | None = None,
    mathlib_search_skill: SkillDefinition | None = None,
    lean_goal_solver_skill: SkillDefinition | None = None,
    prover_client: ModelClient | None = None,
) -> SolveResult:
    """Formalize -> (if it survives triviality) counterexample search ->
    (if not refuted) full proof search, on exactly `claim`. Every stage is
    recorded in the ledger against one Candidate row representing the claim
    itself, so the existing UI/CLI (discoveries list, mindmap) render a
    solve-mode result identically to an explore-mode one -- no schema
    change needed. `run_code_skill=None` skips counterexample search
    (proceeds straight to proving); the CLI enforces it's present before
    calling this, matching `hanno discoveries verify`'s existing behavior.

    `mathlib_search_skill` and `lean_goal_solver_skill`, if given, are both
    offered as tools during BOTH formalize and prove (EXCAVATOR_MIGRATION_PLAN.md
    Phase 6's grounding tools) -- the first lets the model check a lemma/tactic
    name against the real Mathlib source before betting a proof attempt on
    it; the second lets it check whether a real, standard closing tactic
    actually solves a stuck sub-goal via a real compile, instead of guessing
    a single tactic (e.g. using `omega` on a goal involving exponentiation,
    which it structurally cannot reason about). `prover_client`, if given,
    handles only the prove step (a separate, e.g. stronger/dedicated model
    per Phase 6's per-role routing); formalize and counterexample search
    always use the main `client`."""
    candidate = Candidate(
        seed_id=seed_id,
        domain="mathematics",
        temperature=0.0,
        prompt_variant=SOLVE_PROMPT_VARIANT,
        model=model_name,
        prompt_text=claim,
        raw_text=claim,
        parse_status="ok",
        core_claim=claim,
        formal_statement=claim,
        duplicate_status="NOVEL",
        exploration_signal="continue",
        claim_type="structural",
    )
    ledger.insert_candidate(candidate)
    candidate_dict = {"core_claim": claim, "formal_statement": claim}

    grounding_skills = [s for s in (mathlib_search_skill, lean_goal_solver_skill) if s is not None]
    grounding_tools = grounding_skills or None
    grounding_skills_by_name = {s.name: s for s in grounding_skills}
    grounding_invocations: list[ToolInvocation] = []

    def grounding_tool_executor(name: str, tool_input: dict) -> str:
        skill = grounding_skills_by_name.get(name)
        if skill is None:
            return f"error: unknown tool {name!r}"
        input_text = str((tool_input or {}).get("input", ""))
        res = run_skill(skill, input_text)
        grounding_invocations.append(
            ToolInvocation(
                candidate_id=candidate.id,
                skill_name=skill.name,
                input_text=input_text,
                stdout=res.stdout,
                stderr=res.stderr,
                exit_code=res.exit_code,
                duration_ms=res.duration_ms,
                timed_out=res.timed_out,
            )
        )
        return format_for_model(res)

    grounding_executor = grounding_tool_executor if grounding_skills else None

    outcome = await autoformalize_with_retry(
        candidate_dict,
        client,
        project_dir,
        lean_timeout_s,
        max_translation_attempts,
        tools=grounding_tools,
        tool_executor=grounding_executor,
    )
    for inv in grounding_invocations:
        ledger.insert_tool_invocation(inv)
    grounding_invocations.clear()
    label = classify_result(outcome.lean_code, outcome.check)
    ledger.insert_lean_verification(
        LeanVerification(
            candidate_id=candidate.id,
            lean_code=outcome.lean_code,
            translation_attempt_number=outcome.attempts,
            lean_stdout=outcome.check.stdout if outcome.check else None,
            lean_stderr=outcome.check.stderr if outcome.check else None,
            result=label,
            execution_time_ms=outcome.check.duration_ms if outcome.check else None,
        )
    )

    if label is not None:
        # UNTRANSLATABLE / MALFORMED / TIMEOUT / KNOWN -- did not survive
        # the triviality filter, so there is nothing left to prove.
        stage = "trivial" if label == "KNOWN" else label.lower()
        return SolveResult(
            candidate_id=candidate.id,
            triviality_lean_code=outcome.lean_code,
            triviality_result=label,
            refuted=False,
            counterexample_output=None,
            proved=False,
            proof_code=None,
            discovery_id=None,
            stage_reached=stage,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )

    # Survived the triviality filter -- record it as a Discovery, same as
    # explore mode's _check_promising, so `hanno discoveries list` and the
    # mindmap render a solve-mode survivor identically to an explore-mode one.
    discovery = Discovery(
        candidate_id=candidate.id,
        seed_id=seed_id,
        domain="mathematics",
        statement=claim,
        formal_statement=outcome.lean_code,
        verification_result="SURVIVED",
        lean_verified=True,
        human_reviewed=False,
    )
    ledger.insert_discovery(discovery)

    total_input = outcome.input_tokens
    total_output = outcome.output_tokens
    assert outcome.lean_code is not None  # a survivor has a compiled statement

    if run_code_skill is not None:
        ce = await search_for_counterexample(claim, outcome.lean_code, client, run_code_skill)
        total_input += ce.input_tokens
        total_output += ce.output_tokens
        ledger.insert_tool_invocation(
            ToolInvocation(
                candidate_id=candidate.id,
                skill_name=run_code_skill.name,
                input_text=ce.program,
                stdout=ce.skill_result.stdout,
                stderr=ce.skill_result.stderr,
                exit_code=ce.skill_result.exit_code,
                duration_ms=ce.skill_result.duration_ms,
                timed_out=ce.skill_result.timed_out,
                phase="counterexample",
            )
        )
        if ce.refuted:
            return SolveResult(
                candidate_id=candidate.id,
                triviality_lean_code=outcome.lean_code,
                triviality_result=label,
                refuted=True,
                counterexample_output=ce.skill_result.stdout,
                proved=False,
                proof_code=None,
                discovery_id=discovery.id,
                stage_reached="refuted",
                input_tokens=total_input,
                output_tokens=total_output,
            )

    proof = await prove_with_retry(
        outcome.lean_code,
        prover_client or client,
        project_dir,
        lean_timeout_s,
        max_proof_attempts,
        tools=grounding_tools,
        tool_executor=grounding_executor,
    )
    for inv in grounding_invocations:
        ledger.insert_tool_invocation(inv)
    total_input += proof.input_tokens
    total_output += proof.output_tokens
    ledger.insert_lean_verification(
        LeanVerification(
            candidate_id=candidate.id,
            lean_code=proof.proof_code,
            translation_attempt_number=proof.attempts,
            lean_stdout=proof.check.stdout if proof.check else None,
            lean_stderr=proof.check.stderr if proof.check else None,
            result="VERIFIED" if proof.proved else None,
            execution_time_ms=proof.check.duration_ms if proof.check else None,
        )
    )

    return SolveResult(
        candidate_id=candidate.id,
        triviality_lean_code=outcome.lean_code,
        triviality_result=label,
        refuted=False,
        counterexample_output=None,
        proved=proof.proved,
        proof_code=proof.proof_code,
        discovery_id=discovery.id,
        stage_reached="proved" if proof.proved else "unresolved",
        input_tokens=total_input,
        output_tokens=total_output,
    )
