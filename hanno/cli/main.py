"""Hanno CLI entrypoint — registers all subcommands."""
from __future__ import annotations

import typer

from hanno.cli.commands import (
    arms, benchmark, discoveries, export, lean_pilot, mine, report, seeds, skills, solve, stats, trace, ui,
)

app = typer.Typer(help="Project Hanno — knowledge mining system.")

app.command("mine")(mine.mine)
app.command("solve")(solve.solve)
app.command("benchmark")(benchmark.benchmark)
app.command("stats")(stats.stats)
app.command("report")(report.report)
app.command("export")(export.export)
app.command("trace")(trace.trace)
app.command("arms")(arms.arms)
app.command("skills")(skills.skills_list)
app.command("ui")(ui.ui)
app.command("lean-pilot")(lean_pilot.lean_pilot)
app.add_typer(seeds.app, name="seeds")
app.add_typer(discoveries.app, name="discoveries")


if __name__ == "__main__":
    app()
