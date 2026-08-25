"""`hanno discoveries` -- lists candidates flagged for human review by
`hanno mine --until-promising` (a Discovery row per candidate that cleared
the Lean triviality filter), and runs deep verification on them before they
reach a person: counterexample search first (a real counterexample is a
definitive rejection, no human needed for that verdict), then, only for
survivors, a real Lean proof-search attempt (hanno/lean/prove.py) --
distinct from the triviality filter's one-shot `:= by sorry` translation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from hanno.cli.context import build_context
from hanno.excavator.client_factory import build_client, build_prover_client
from hanno.ledger.models import LeanVerification, ToolInvocation
from hanno.lean.prove import prove_with_retry
from hanno.skills.registry import load_skill_files
from hanno.skills.runner import format_for_model, run_skill
from hanno.ui.secrets import load_api_key
from hanno.verification.counterexample import COUNTEREXAMPLE_MARKER, search_for_counterexample

app = typer.Typer(help="List and deep-verify discoveries flagged by `hanno mine --until-promising`.")


def _status(ledger, discovery: dict) -> str:
    invocations = ledger.list_tool_invocations_by_candidate(discovery["candidate_id"])
    if not invocations:
        return "not yet verified"
    if any(_COUNTEREXAMPLE_MARKER in (inv["stdout"] or "") for inv in invocations):
        return "REFUTED (counterexample found)"
    verifications = ledger.list_lean_verifications_by_candidate(discovery["candidate_id"])
    if any(v["result"] == "VERIFIED" for v in verifications):
        return "PROVEN (complete Lean proof found)"
    return "unresolved (no counterexample, but no automated proof either)"


@app.command("list")
def list_discoveries():
    ctx = build_context()
    discoveries = ctx.ledger.list_discoveries()
    if not discoveries:
        typer.echo("No discoveries yet -- mine with `hanno mine --until-promising` to find one, "
                    "or `hanno lean-pilot` to check existing candidates.")
        return
    for d in discoveries:
        status = _status(ctx.ledger, d)
        statement = (d["statement"] or "")[:80]
        typer.echo(f"{d['id'][:8]}  [{status}]  {statement}")


@app.command("verify")
def verify(
    limit: int = typer.Option(10, "--limit", help="Max unverified discoveries to deep-verify."),
):
    """Counterexample search, then (for survivors) a real proof-search
    attempt, for every discovery not yet deep-verified."""
    ctx = build_context()
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
            "ERROR: the run_code skill (skills/run_code.yaml) was not found -- "
            "counterexample search needs it.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    mathlib_search_skill = next((s for s in skills if s.name == "mathlib_search"), None)
    if mathlib_search_skill is None:
        typer.secho(
            "WARNING: the mathlib_search skill was not found -- "
            "proving proceeds without lemma-name grounding.",
            fg=typer.colors.YELLOW,
        )
    lean_goal_solver_skill = next((s for s in skills if s.name == "lean_goal_solver"), None)
    if lean_goal_solver_skill is None:
        typer.secho(
            "WARNING: the lean_goal_solver skill was not found -- "
            "proving proceeds without tactic-selection grounding.",
            fg=typer.colors.YELLOW,
        )

    discoveries = ctx.ledger.list_discoveries()
    pending = [
        d for d in discoveries if not ctx.ledger.list_tool_invocations_by_candidate(d["candidate_id"])
    ][:limit]
    if not pending:
        typer.echo("Nothing to verify.")
        return

    typer.echo(f"Deep-verifying {len(pending)} discovery(ies)...")
    client = build_client(ctx.config, api_key=load_api_key(ctx.config.excavator.provider), max_tokens=3000)
    prover_client = build_prover_client(
        ctx.config, api_key=load_api_key(ctx.config.excavator.prover_provider or ctx.config.excavator.provider),
        max_tokens=3000,
    )
    timeout_s = ctx.config.lean_verifier.lean_timeout_seconds
    max_proof_attempts = ctx.config.lean_verifier.max_proof_attempts
    grounding_skills = [s for s in (mathlib_search_skill, lean_goal_solver_skill) if s is not None]
    grounding_tools = grounding_skills or None
    grounding_skills_by_name = {s.name: s for s in grounding_skills}

    async def _run():
        for d in pending:
            typer.echo(f"\n{d['id'][:8]}  candidate {d['candidate_id'][:8]}")
            typer.echo("  searching for a counterexample...")

            ce = await search_for_counterexample(
                d["statement"] or "", d["formal_statement"] or "", client, run_code_skill,
            )
            ctx.ledger.insert_tool_invocation(ToolInvocation(
                candidate_id=d["candidate_id"], skill_name=run_code_skill.name,
                input_text=ce.program, stdout=ce.skill_result.stdout, stderr=ce.skill_result.stderr,
                exit_code=ce.skill_result.exit_code, duration_ms=ce.skill_result.duration_ms,
                timed_out=ce.skill_result.timed_out,
            ))
            if ce.refuted:
                typer.secho(f"  REFUTED: {ce.skill_result.stdout[:300]}", fg=typer.colors.RED)
                continue

            typer.echo("  no counterexample found -- attempting a real proof (this can take a while)...")

            def grounding_tool_executor(name: str, tool_input: dict, _discovery=d) -> str:
                skill = grounding_skills_by_name.get(name)
                if skill is None:
                    return f"error: unknown tool {name!r}"
                input_text = str((tool_input or {}).get("input", ""))
                res = run_skill(skill, input_text)
                ctx.ledger.insert_tool_invocation(ToolInvocation(
                    candidate_id=_discovery["candidate_id"], skill_name=skill.name,
                    input_text=input_text, stdout=res.stdout, stderr=res.stderr,
                    exit_code=res.exit_code, duration_ms=res.duration_ms, timed_out=res.timed_out,
                ))
                return format_for_model(res)

            proof = await prove_with_retry(
                d["formal_statement"], prover_client or client, project_dir, timeout_s, max_proof_attempts,
                tools=grounding_tools, tool_executor=grounding_tool_executor if grounding_skills else None,
            )
            ctx.ledger.insert_lean_verification(LeanVerification(
                candidate_id=d["candidate_id"],
                lean_code=proof.proof_code,
                translation_attempt_number=proof.attempts,
                lean_stdout=proof.check.stdout if proof.check else None,
                lean_stderr=proof.check.stderr if proof.check else None,
                result="VERIFIED" if proof.proved else None,
                execution_time_ms=proof.check.duration_ms if proof.check else None,
            ))
            if proof.proved:
                # This fires purely on "a complete, compiling, sorry-free proof
                # was found" -- deliberately independent of novelty/significance.
                # A well-known fact reproven this way is still a real signal
                # that the pipeline can go all the way to a checkable proof,
                # which is the thing worth flagging here, not literature novelty.
                typer.echo("")
                typer.secho("=" * 70, fg=typer.colors.GREEN, bold=True)
                typer.secho(
                    f"SOLVED -- a complete, compiling Lean proof was found "
                    f"(attempts={proof.attempts}). Flagged regardless of whether "
                    f"this claim is novel in the literature.",
                    fg=typer.colors.GREEN, bold=True,
                )
                typer.secho(f"  statement: {(d['statement'] or '')[:200]}", fg=typer.colors.GREEN)
                typer.secho(
                    f"  discovery {d['id'][:8]} | candidate {d['candidate_id'][:8]}",
                    fg=typer.colors.GREEN,
                )
                typer.secho("=" * 70, fg=typer.colors.GREEN, bold=True)
                typer.echo("")
            else:
                typer.echo(
                    f"  could not find a complete proof automatically (attempts={proof.attempts}) "
                    f"-- an honest unresolved outcome, still a real candidate for human review."
                )
        await client.close()
        if prover_client is not None:
            await prover_client.close()

    asyncio.run(_run())
