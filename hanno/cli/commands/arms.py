"""`hanno arms` — leaderboard of (model, temperature, prompt_variant,
reasoning_effort) combinations by average path_score, so the adaptive
selection in ExcavatorPipeline isn't a black box."""
from __future__ import annotations

import typer

from hanno.cli.context import build_context

MATH_DOMAIN = "mathematics"


def arms():
    ctx = build_context()
    stats = ctx.ledger.get_arm_stats(MATH_DOMAIN)
    if not stats:
        typer.echo("No arm history yet — run `hanno mine` at least once.")
        return

    stats.sort(key=lambda s: s["avg_score"], reverse=True)
    typer.echo(f"{'avg_score':>9}  {'pulls':>5}  {'temp':>5}  {'reasoning':>9}  variant")
    for s in stats:
        typer.echo(
            f"{s['avg_score']:>9.3f}  {s['pulls']:>5}  {s['temperature']:>5}  "
            f"{s['reasoning_effort']:>9}  {s['prompt_variant']} ({s['model']})"
        )
