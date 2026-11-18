"""`hanno skills` — list the miner-registered tools the Excavator model can
call during mining, including disabled ones, so what's exposed to the model
is never a mystery."""

from __future__ import annotations

import typer

from hanno.config import load_config
from hanno.skills.registry import load_skill_files


def skills_list() -> None:
    config = load_config()
    directory = config.skills.directory
    if not config.skills.enabled:
        typer.secho(
            "Skills are disabled in config (skills.enabled: false).", fg=typer.colors.YELLOW
        )
    all_skills = load_skill_files(directory)
    if not all_skills:
        typer.echo(
            f"No skills found in '{directory}/'. Add a YAML file there to register a tool "
            f"the model can call during mining — see the user manual's Skills section."
        )
        return
    for s in all_skills:
        status = "enabled " if s.enabled else "DISABLED"
        typer.secho(
            f"[{status}] {s.name}",
            fg=typer.colors.GREEN if s.enabled else typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"  {s.description.strip()}")
        typer.echo(
            f"  command: {' '.join(s.command)}  |  input: {s.input_mode}  |  "
            f"timeout: {s.timeout_seconds}s  |  output cap: {s.max_output_chars} chars"
        )
        typer.echo("")
