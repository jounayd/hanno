"""`hanno export` — full Ledger dump to JSON."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from hanno.cli.context import build_context


def export(
    path: str = typer.Option(None, "--path", help="Output file path (defaults to config export_path)."),
):
    ctx = build_context()
    if path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = str(Path(ctx.config.ledger.export_path) / f"hanno_export_{timestamp}.json")
    ctx.ledger.export_json(path)
    typer.secho(f"Exported ledger to {path}", fg=typer.colors.GREEN)
