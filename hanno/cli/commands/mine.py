"""`hanno mine` — run an excavation cycle against a seed (Step 3, math only)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from hanno.cli.context import build_context
from hanno.excavator.client_factory import build_client
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.ui.secrets import load_api_key


def _print_cycle_summary(result) -> None:
    typer.echo(
        f"cycle complete: requested={result.candidates_requested} "
        f"ok={result.candidates_parsed_ok} failed={result.candidates_failed} "
        f"exact_dup={result.candidates_exact_duplicate} near_dup={result.candidates_near_duplicate} "
        f"cost=${result.total_cost_usd:.4f} "
        f"tokens_in={result.total_input_tokens} tokens_out={result.total_output_tokens} "
        f"avg_path_score={result.avg_path_score:.3f}"
    )
    if result.halted_on_spend_limit:
        typer.secho("HALTED: daily spend limit reached mid-cycle.", fg=typer.colors.YELLOW)

    if result.branch_point_count or result.dead_end_count:
        typer.echo(
            f"exploration signals: branch_point={result.branch_point_count} "
            f"dead_end={result.dead_end_count}"
        )
    if result.suggested_seed_texts:
        typer.secho("SUGGESTED FOLLOW-UP SEEDS (review before submitting):", fg=typer.colors.CYAN)
        for s in result.suggested_seed_texts:
            typer.echo(f"  - {s}")
    if result.reseed_recommended:
        typer.secho(f"RESEED RECOMMENDED: {result.reseed_reason}", fg=typer.colors.YELLOW)


def _print_iterative_summary(result) -> None:
    if result.resumed_from_iteration:
        typer.secho(
            f"resuming from previous run(s): {result.resumed_from_iteration} iteration(s) "
            f"already in the ledger for this seed — continuing at iteration "
            f"{result.resumed_from_iteration + 1} (use --fresh to restart from iteration 1)",
            fg=typer.colors.CYAN,
        )
    for summary in result.iterations:
        n_threads = len(summary.cycle_results)
        typer.secho(
            f"=== Iteration {summary.iteration} ({n_threads} active thread{'s' if n_threads != 1 else ''}) ===",
            bold=True,
        )
        for i, cr in enumerate(summary.cycle_results, start=1):
            typer.echo(
                f"  thread {i}: requested={cr.candidates_requested} ok={cr.candidates_parsed_ok} "
                f"failed={cr.candidates_failed} cost=${cr.total_cost_usd:.4f} "
                f"avg_path_score={cr.avg_path_score:.3f}"
            )
        if summary.stopped_early:
            typer.secho(f"  -> stopped early: {summary.stop_reason}", fg=typer.colors.YELLOW)
        elif summary.beam_after:
            scores = ", ".join(f"{t.best_path_score:.2f}" for t in summary.beam_after)
            typer.echo(f"  -> {len(summary.beam_after)} candidate(s) carried forward (scores: {scores})")

    typer.echo(
        f"\nTOTAL: candidates={result.total_candidates_parsed_ok} cost=${result.total_cost_usd:.4f} "
        f"tokens_in={result.total_input_tokens} tokens_out={result.total_output_tokens}"
    )

    if result.promising_found:
        typer.secho(f"\nPROMISING CANDIDATE FOUND: {result.promising_reason}", fg=typer.colors.GREEN, bold=True)
        typer.secho(
            f"Independently cleared the Lean triviality filter — mining stopped early. "
            f"Recorded as a discovery for human review. Full detail: "
            f"hanno trace {result.seed_id} --lineage {result.promising_candidate_id}",
            fg=typer.colors.GREEN,
        )
    elif result.strong_path_found:
        typer.secho(f"\nSTRONG PATH FOUND: {result.strong_path_reason}", fg=typer.colors.GREEN, bold=True)
        typer.secho(
            f"HUMAN REVIEW RECOMMENDED — full detail: hanno trace <seed_id> --lineage {result.best_lineage[-1]}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(
            "\nNo sustained strong path yet — this is expected and not a failure; "
            "review hanno trace as usual before deciding on next steps."
        )


def mine(
    seed: str = typer.Option(..., "--seed", help="Seed ID to mine."),
    candidates: int = typer.Option(50, "--candidates", help="Number of candidates to generate per iteration."),
    depth: int = typer.Option(
        1, "--depth",
        help="Iterative mining depth. 1 (default) = a single ordinary cycle, unchanged behavior. "
             ">1 refines the most promising candidate(s) from each iteration instead of re-mining "
             "the seed from scratch -- cost multiplies roughly by --top-k per additional iteration.",
    ),
    top_k: int = typer.Option(
        None, "--top-k",
        help="Beam width for --depth > 1: how many top candidates carry forward each iteration "
             "(default from config excavator.iteration_top_k).",
    ),
    fresh: bool = typer.Option(
        False, "--fresh",
        help="For --depth > 1: restart the beam search from the original seed at iteration 1 "
             "instead of the default behavior of resuming from the previous run's best "
             "candidates and iteration count (e.g. depth 3 then depth 2 = iterations 1-5).",
    ),
    until_promising: bool = typer.Option(
        False, "--until-promising",
        help="Keep iterating (up to --depth as a safety ceiling, not a fixed target) until a "
             "candidate independently clears the Lean triviality filter (compiles, isn't "
             "trivially closed by exact?/simp), then stop and record it as a discovery for "
             "human review. Requires the local Lean toolchain (see hanno lean-pilot).",
    ),
):
    ctx = build_context()
    seed_row = ctx.ledger.get_seed(seed)
    if seed_row is None:
        typer.secho(f"ERROR: seed {seed} not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if seed_row["domain"] != "mathematics":
        typer.secho(
            "ERROR: only the mathematics domain is implemented in Step 3.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    if depth < 1:
        typer.secho("ERROR: --depth must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if until_promising and not Path(ctx.config.lean_verifier.mathlib_cache_path).exists():
        typer.secho(
            f"ERROR: --until-promising needs the local Lean toolchain, but "
            f"{ctx.config.lean_verifier.mathlib_cache_path} was not found. "
            "Set it up first (see hanno lean-pilot).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    client = build_client(ctx.config, api_key=load_api_key(ctx.config.excavator.provider))
    pipeline = ExcavatorPipeline(ctx.ledger, ctx.config, ctx.fingerprint_engine, client=client, skills=ctx.skills)
    if ctx.skills:
        typer.echo(f"skills active: {', '.join(s.name for s in ctx.skills)}")

    if depth == 1 and not until_promising:
        async def _run_single():
            try:
                return await pipeline.run_cycle(seed, seed_row["text"], candidates)
            finally:
                await pipeline.close()

        result = asyncio.run(_run_single())
        _print_cycle_summary(result)
    else:
        async def _run_iterative():
            try:
                return await pipeline.run_iterative_mining(
                    seed, seed_row["text"], candidates, depth, top_k=top_k, resume=not fresh,
                    until_promising=until_promising,
                )
            finally:
                await pipeline.close()

        result = asyncio.run(_run_iterative())
        _print_iterative_summary(result)
