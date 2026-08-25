"""`hanno stats` — basic cycle/cost statistics (full version arrives Step 7)."""
from __future__ import annotations

import typer

from hanno.cli.context import build_context


def stats():
    ctx = build_context()
    cycles = ctx.ledger.list_excavation_cycles()
    if not cycles:
        typer.echo("No excavation cycles recorded yet.")
        return

    total_requested = sum(c["candidates_requested"] for c in cycles)
    total_ok = sum(c["candidates_parsed_ok"] for c in cycles)
    total_failed = sum(c["candidates_failed"] for c in cycles)
    total_cost = sum(c["total_cost_usd"] for c in cycles)
    total_input_tokens = sum(c["total_input_tokens"] for c in cycles)
    total_output_tokens = sum(c["total_output_tokens"] for c in cycles)
    total_calls = total_ok + total_failed

    typer.echo(f"Cycles run:            {len(cycles)}")
    typer.echo(f"Candidates requested:  {total_requested}")
    typer.echo(f"Candidates parsed ok:  {total_ok}")
    typer.echo(f"Candidates failed:     {total_failed}")
    parse_rate = (total_ok / total_requested * 100) if total_requested else 0
    typer.echo(f"Parse success rate:    {parse_rate:.1f}%")
    typer.echo(f"Total cost:            ${total_cost:.4f}")
    typer.echo(f"Today's spend:         ${ctx.ledger.get_today_spend_usd():.4f}")
    typer.echo(f"Input tokens used:     {total_input_tokens:,}")
    typer.echo(f"Output tokens used:    {total_output_tokens:,}")
    if total_calls:
        typer.echo(f"Avg tokens/candidate:  {(total_input_tokens + total_output_tokens) / total_calls:.0f}")
