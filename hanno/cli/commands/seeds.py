"""`hanno seeds` — submit and list seeds (Step 2)."""

from __future__ import annotations

import typer

from hanno.cli.context import build_context
from hanno.seeds.manager import SeedManager

app = typer.Typer(help="Manage the seed queue.")

VALID_SEED_TYPES = ("human", "ai-generated", "derivative")


@app.command("add")
def add(
    text: str = typer.Argument(..., help="Seed concept text (10-200 words)."),
    domain: str = typer.Option(
        ..., "--domain", help="mathematics | science | philosophy | cross-domain"
    ),
    submitter_id: str = typer.Option(None, "--submitter", help="Human submitter identifier."),
    type: str = typer.Option(
        "human",
        "--type",
        help="human | ai-generated | derivative — use ai-generated when submitting a "
        "suggested_seeds entry surfaced by `hanno mine`, so it's traceable as such.",
    ),
) -> None:
    if type not in VALID_SEED_TYPES:
        typer.secho(
            f"REJECTED: invalid --type '{type}'. Must be one of: {', '.join(VALID_SEED_TYPES)}.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    ctx = build_context()
    manager = SeedManager(ctx.ledger, ctx.fingerprint_engine)
    result = manager.submit(text=text, domain=domain, type=type, submitter_id=submitter_id)
    if not result.ok:
        typer.secho(f"REJECTED: {result.feedback}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(f"OK: seed {result.seed_id} queued.", fg=typer.colors.GREEN)
    if result.duplicate_classification in ("NEAR_DUPLICATE", "EXACT_DUPLICATE"):
        typer.secho(f"NOTE: {result.feedback}", fg=typer.colors.YELLOW)


@app.command("list")
def list_seeds(domain: str = typer.Option(None, "--domain")) -> None:
    ctx = build_context()
    manager = SeedManager(ctx.ledger, ctx.fingerprint_engine)
    queue = manager.list_queue(domain=domain)
    if not queue:
        typer.echo("No seeds in queue.")
        return
    for s in queue:
        typer.echo(
            f"[{s['status']:9}] {s['id']}  ({s['domain']}, {s['type']})  "
            f"cycles={s['cycles_run']} candidates={s['candidates_produced']} "
            f"discoveries={s['discoveries_attributed']}  :: {s['text'][:80]}"
        )
