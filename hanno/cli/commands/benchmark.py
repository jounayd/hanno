"""`hanno benchmark` -- Phase 0 (EXCAVATOR_MIGRATION_PLAN.md): runs the fixed
solvable-seed benchmark (benchmarks/solve_benchmark.yaml) through SOLVE mode
and reports solve-rate + real $ cost. This is the number every later
migration phase (2-6) is judged against."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from hanno.cli.context import build_context
from hanno.excavator.client_factory import build_client, build_prover_client
from hanno.skills.registry import load_skill_files
from hanno.solve.benchmark import load_benchmark_cases, run_benchmark
from hanno.ui.secrets import load_api_key

_STAGE_COLORS = {
    "proved": typer.colors.GREEN,
    "unresolved": typer.colors.YELLOW,
    "refuted": typer.colors.RED,
    "trivial": typer.colors.YELLOW,
    "malformed": typer.colors.RED,
    "untranslatable": typer.colors.RED,
    "timeout": typer.colors.RED,
}


def benchmark():
    """Run the fixed benchmark of solvable seeds through SOLVE mode and
    report pass/fail + real $ cost per case, plus a summary. Each run
    creates fresh Seed rows (submitter_id="benchmark") -- safe to re-run
    repeatedly, e.g. before/after a migration-plan phase change."""
    ctx = build_context()
    project_dir = Path(ctx.config.lean_verifier.mathlib_cache_path)
    if not project_dir.exists():
        typer.secho(
            f"ERROR: Lean project directory {project_dir} not found. "
            "Set up the local toolchain first (see hanno lean-pilot).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    cases = load_benchmark_cases()
    skills = load_skill_files(ctx.config.skills.directory)
    run_code_skill = next((s for s in skills if s.name == "run_code"), None)
    if run_code_skill is None:
        typer.secho(
            "WARNING: the run_code skill was not found -- skipping counterexample "
            "search for every case, proceeding straight to proof search.",
            fg=typer.colors.YELLOW,
        )
    mathlib_search_skill = next((s for s in skills if s.name == "mathlib_search"), None)
    if mathlib_search_skill is None:
        typer.secho(
            "WARNING: the mathlib_search skill was not found -- "
            "formalize/prove proceed without lemma-name grounding.",
            fg=typer.colors.YELLOW,
        )
    lean_goal_solver_skill = next((s for s in skills if s.name == "lean_goal_solver"), None)
    if lean_goal_solver_skill is None:
        typer.secho(
            "WARNING: the lean_goal_solver skill was not found -- "
            "prove proceeds without tactic-selection grounding.",
            fg=typer.colors.YELLOW,
        )

    typer.echo(f"Running {len(cases)} benchmark case(s)...\n")
    client = build_client(ctx.config, api_key=load_api_key(ctx.config.excavator.provider), max_tokens=3000)
    prover_client = build_prover_client(
        ctx.config, api_key=load_api_key(ctx.config.excavator.prover_provider or ctx.config.excavator.provider),
        max_tokens=3000,
    )

    async def _run():
        results = await run_benchmark(
            cases, client, project_dir, ctx.ledger,
            lean_timeout_s=ctx.config.lean_verifier.lean_timeout_seconds,
            max_translation_attempts=ctx.config.lean_verifier.max_translation_attempts,
            max_proof_attempts=ctx.config.lean_verifier.max_proof_attempts,
            provider=ctx.config.excavator.provider,
            model_name=ctx.config.excavator.model,
            run_code_skill=run_code_skill,
            mathlib_search_skill=mathlib_search_skill,
            lean_goal_solver_skill=lean_goal_solver_skill,
            prover_client=prover_client,
        )
        await client.close()
        if prover_client is not None:
            await prover_client.close()
        return results

    results = asyncio.run(_run())

    total_cost = 0.0
    solved = 0
    for r in results:
        total_cost += r.cost_usd
        stage = r.solve_result.stage_reached
        if stage == "proved":
            solved += 1
        typer.secho(
            f"  [{r.case.difficulty:>6}] {r.case.name:<32} {stage:<15} (${r.cost_usd:.4f})",
            fg=_STAGE_COLORS.get(stage, None),
        )

    typer.echo("")
    typer.secho(
        f"Solved {solved}/{len(results)} -- total cost ${total_cost:.4f}",
        fg=typer.colors.GREEN if solved == len(results) else typer.colors.YELLOW,
        bold=True,
    )
