"""`hanno solve` -- SOLVE mode (EXCAVATOR_MIGRATION_PLAN.md Phase 1): prove
or disprove EXACTLY one given claim, bypassing the excavator's generation/
exploration loop entirely.

Direct fix for the goal-drift failure mode observed in EXPLORE-mode mining:
on a seed asking to prove "n^11 - n is divisible by 66", every generated
candidate abandoned the concrete claim for progressively more abstract,
unrelated reformulations (a maximality theorem, a fabricated complexity
bound, an invented "Frobenius-Invariant Divisor" framework) -- because the
excavator's standing prompt explicitly asks for claims that are "non-obvious"
and "connect to existing knowledge in a non-trivial way", which structurally
rewards drifting away from the one concrete thing actually asked for. This
command has no such instruction anywhere: the claim is fixed, formalized
faithfully (see hanno/lean/autoformalize.py's strength-preservation rules),
and either proved, refuted, or left honestly unresolved.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from hanno.cli.context import build_context
from hanno.excavator.client_factory import build_client, build_prover_client
from hanno.skills.registry import load_skill_files
from hanno.solve.pipeline import solve_claim
from hanno.ui.secrets import load_api_key


def solve(
    seed_id: str = typer.Argument(..., help="Seed ID to solve (for ledger/UI linkage)."),
    claim: str = typer.Option(
        None, "--claim",
        help="The exact claim to prove/disprove. Defaults to the seed's own text -- pass this "
             "explicitly if the seed text includes background/context you don't want formalized "
             "verbatim (e.g. \"this is the k=N instance of the general fact that...\").",
    ),
):
    """Prove or disprove exactly one claim -- no candidate generation, no
    exploration, no self-rated scoring. Formalizes the claim faithfully,
    searches for a counterexample, and if none is found, attempts a
    complete Lean proof."""
    ctx = build_context()
    seed_row = ctx.ledger.get_seed(seed_id)
    if seed_row is None:
        typer.secho(f"ERROR: seed {seed_id} not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    claim_text = claim or seed_row["text"]

    project_dir = Path(ctx.config.lean_verifier.mathlib_cache_path)
    if not project_dir.exists():
        typer.secho(
            f"ERROR: Lean project directory {project_dir} not found. "
            "Set up the local toolchain first (see hanno lean-pilot).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    skills = load_skill_files(ctx.config.skills.directory)
    run_code_skill = next((s for s in skills if s.name == "run_code"), None)
    if run_code_skill is None:
        typer.secho(
            "WARNING: the run_code skill (skills/run_code.yaml) was not found -- "
            "skipping counterexample search, proceeding straight to proof search.",
            fg=typer.colors.YELLOW,
        )
    mathlib_search_skill = next((s for s in skills if s.name == "mathlib_search"), None)
    if mathlib_search_skill is None:
        typer.secho(
            "WARNING: the mathlib_search skill (skills/mathlib_search.yaml) was not found -- "
            "formalize/prove proceed without lemma-name grounding.",
            fg=typer.colors.YELLOW,
        )
    lean_goal_solver_skill = next((s for s in skills if s.name == "lean_goal_solver"), None)
    if lean_goal_solver_skill is None:
        typer.secho(
            "WARNING: the lean_goal_solver skill (skills/lean_goal_solver.yaml) was not found -- "
            "prove proceeds without tactic-selection grounding.",
            fg=typer.colors.YELLOW,
        )

    typer.echo(f"Solving: {claim_text}")
    client = build_client(ctx.config, api_key=load_api_key(ctx.config.excavator.provider), max_tokens=3000)
    prover_client = build_prover_client(
        ctx.config, api_key=load_api_key(ctx.config.excavator.prover_provider or ctx.config.excavator.provider),
        max_tokens=3000,
    )

    async def _run():
        result = await solve_claim(
            claim_text, seed_id, client, project_dir, ctx.ledger,
            lean_timeout_s=ctx.config.lean_verifier.lean_timeout_seconds,
            max_translation_attempts=ctx.config.lean_verifier.max_translation_attempts,
            max_proof_attempts=ctx.config.lean_verifier.max_proof_attempts,
            model_name=ctx.config.excavator.model,
            run_code_skill=run_code_skill,
            mathlib_search_skill=mathlib_search_skill,
            lean_goal_solver_skill=lean_goal_solver_skill,
            prover_client=prover_client,
        )
        await client.close()
        if prover_client is not None:
            await prover_client.close()
        return result

    typer.echo("  formalizing (faithfully -- no generalization, no weakening)...")
    result = asyncio.run(_run())

    if result.stage_reached in ("untranslatable", "malformed", "timeout"):
        typer.secho(
            f"Could not formalize this claim: {result.triviality_result}", fg=typer.colors.RED,
        )
    elif result.stage_reached == "trivial":
        typer.secho(
            "This claim is trivially closed by exact?/simp -- not a meaningful proof target.",
            fg=typer.colors.YELLOW,
        )
    elif result.stage_reached == "refuted":
        typer.secho("REFUTED -- a counterexample was found:", fg=typer.colors.RED, bold=True)
        typer.echo(f"  {(result.counterexample_output or '')[:400]}")
    elif result.stage_reached == "proved":
        typer.echo("")
        typer.secho("=" * 70, fg=typer.colors.GREEN, bold=True)
        typer.secho("SOLVED -- a complete, compiling Lean proof was found.", fg=typer.colors.GREEN, bold=True)
        typer.echo(result.proof_code)
        typer.secho("=" * 70, fg=typer.colors.GREEN, bold=True)
    else:  # unresolved
        typer.echo(
            "Survived the triviality filter and no counterexample was found, but no complete "
            "proof was found automatically -- an honest unresolved outcome, a real candidate for "
            "human review."
        )
        typer.echo(f"  hanno trace {seed_id} --lineage {result.candidate_id}")
