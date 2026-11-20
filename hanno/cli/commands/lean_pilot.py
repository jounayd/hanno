"""`hanno lean-pilot` -- Step 6 pilot: validate whether a Lean-based
triviality filter (exact?/simp) can separate trivial from non-trivial
candidates, using candidates already mined in real runs. No new mining --
a handful of one-shot translation calls (hanno/lean/autoformalize.py) plus
a local Lean toolchain check (hanno/lean/checker.py), writing real
lean_verifications rows (previously unused since Step 1).
"""

from __future__ import annotations

from pathlib import Path

import typer

from hanno.cli.context import build_context, run_async
from hanno.excavator.client_factory import build_client
from hanno.lean.autoformalize import autoformalize_with_retry
from hanno.lean.checker import classify_result
from hanno.ledger.models import LeanVerification
from hanno.ui.secrets import load_api_key


def lean_pilot(
    limit: int = typer.Option(50, "--limit", help="Max untested mathematics candidates to check."),
) -> None:
    ctx = build_context()
    project_dir = Path(ctx.config.lean_verifier.mathlib_cache_path)
    if not project_dir.exists():
        typer.secho(
            f"ERROR: Lean project directory {project_dir} not found. "
            "Set up the local toolchain first (elan + lake init + lake exe cache get).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    candidates = ctx.ledger.list_mathematics_candidates_without_lean_check(limit)
    if not candidates:
        typer.echo("No untested mathematics candidates found.")
        return

    typer.echo(f"Checking {len(candidates)} candidate(s)...")
    client = build_client(
        ctx.config,
        api_key=load_api_key(ctx.config.excavator.provider),
        max_tokens=1500,
        ledger=ctx.ledger,
    )
    timeout_s = ctx.config.lean_verifier.lean_timeout_seconds
    max_attempts = ctx.config.lean_verifier.max_translation_attempts

    counts: dict[str, int] = {}
    survivors: list[dict] = []

    async def _run() -> None:
        for c in candidates:
            outcome = await autoformalize_with_retry(
                c, client, project_dir, timeout_s, max_attempts
            )
            check = outcome.check
            label = classify_result(outcome.lean_code, check)

            ctx.ledger.insert_lean_verification(
                LeanVerification(
                    candidate_id=c["id"],
                    lean_code=outcome.lean_code,
                    translation_attempt_number=outcome.attempts,
                    lean_stdout=check.stdout if check else None,
                    lean_stderr=check.stderr if check else None,
                    result=label,
                    execution_time_ms=check.duration_ms if check else None,
                )
            )
            display_label = label or "SURVIVED"
            counts[display_label] = counts.get(display_label, 0) + 1
            if label is None:
                survivors.append(c)
            typer.echo(f"  {c['id'][:8]}  {display_label}  (attempts={outcome.attempts})")
        await client.close()

    run_async(_run())

    typer.echo("\n=== Summary ===")
    for display_label, n in sorted(counts.items()):
        typer.echo(f"  {display_label}: {n}")

    if survivors:
        typer.secho(
            f"\n{len(survivors)} candidate(s) survived the filter (compiles, non-trivial, unresolved):",
            fg=typer.colors.GREEN,
        )
        for c in survivors:
            typer.echo(f"  - {c['core_claim']}")
