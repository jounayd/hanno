"""`hanno ui` — start the local web UI: miner + skill settings, and a live
view of a seed's mining trace (hanno/ui/server.py)."""
from __future__ import annotations

import typer


def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Local-only by default."),
    port: int = typer.Option(8420, "--port", help="Port to serve the UI on."),
):
    import uvicorn

    typer.secho(f"Starting Hanno UI at http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    uvicorn.run("hanno.ui.server:app", host=host, port=port)
