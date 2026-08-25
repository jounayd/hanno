"""`hanno trace` — full mining trace for a seed: every prompt actually sent
to the model and the raw result it returned, in order, for both human and
AI-generated/derivative seeds. This is the review surface for "how the
Excavator is trying to find a discovery" — nothing here is summarized away."""
from __future__ import annotations

import json as json_module

import typer

from hanno.cli.context import build_context
from hanno.ledger.export import build_trace_export

TRUNCATE_CHARS = 400


def _clip(text: str | None, full: bool) -> str:
    if not text:
        return "(empty)"
    if full or len(text) <= TRUNCATE_CHARS:
        return text
    return text[:TRUNCATE_CHARS] + f"... [{len(text) - TRUNCATE_CHARS} more chars, use --full]"


def _walk_lineage(all_by_id: dict[str, dict], candidate_id: str) -> list[dict]:
    chain = []
    current = all_by_id.get(candidate_id)
    seen = set()
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        chain.append(current)
        current = all_by_id.get(current["refined_from_candidate_id"])
    return list(reversed(chain))


def _write_mindmap_json(path: str, seed_row: dict, status: str, candidates: list[dict], ledger) -> None:
    payload = build_trace_export(seed_row, status, candidates, ledger)
    with open(path, "w") as f:
        json_module.dump(payload, f, indent=2, default=str)


def trace(
    seed_id: str = typer.Argument(..., help="Seed ID to trace."),
    full: bool = typer.Option(False, "--full", help="Show untruncated prompt/response text."),
    limit: int = typer.Option(None, "--limit", help="Show only the first N candidates."),
    lineage: str = typer.Option(
        None, "--lineage",
        help="Show only one candidate's refinement chain (iterative mining, --depth > 1): "
             "walks refined_from_candidate_id back to the original iteration.",
    ),
    json_path: str = typer.Option(
        None, "--json",
        help="Write the trace as JSON to this path instead of printing it "
             "(for hanno/viz/trace_mindmap.html) — includes the full seed and every candidate.",
    ),
):
    ctx = build_context()
    seed_row = ctx.ledger.get_seed(seed_id)
    if seed_row is None:
        typer.secho(f"ERROR: seed {seed_id} not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    status = ctx.ledger.get_seed_current_status(seed_id)
    all_candidates = ctx.ledger.list_candidates_by_seed(seed_id)

    if json_path:
        _write_mindmap_json(json_path, seed_row, status, all_candidates, ctx.ledger)
        typer.secho(f"OK: wrote {len(all_candidates)} candidate(s) to {json_path}", fg=typer.colors.GREEN)
        return

    typer.secho(f"SEED {seed_row['id']}  [{seed_row['type']}, {seed_row['domain']}, {status}]", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"submitted_at={seed_row['submitted_at']} submitter={seed_row['submitter_id']}")
    typer.echo(f"text: {seed_row['text']}")
    typer.echo("")

    if not all_candidates:
        typer.echo("No mining attempts recorded for this seed yet.")
        return

    if lineage:
        all_by_id = {c["id"]: c for c in all_candidates}
        if lineage not in all_by_id:
            typer.secho(f"ERROR: candidate {lineage} not found for this seed.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        candidates = _walk_lineage(all_by_id, lineage)
    else:
        candidates = all_candidates

    if limit is not None:
        candidates = candidates[:limit]

    for i, c in enumerate(candidates, start=1):
        numeric_check_display = {0: "FAILED", 1: "passed", None: "n/a"}[c["numeric_check_result"]]
        typer.secho(
            f"--- attempt {i}/{len(candidates)}  id={c['id']}  iteration={c['iteration']}  "
            f"variant={c['prompt_variant']}  temp={c['temperature']}  reasoning={c['reasoning_effort']}  "
            f"parse={c['parse_status']}  dup={c['duplicate_status']}  "
            f"signal={c['exploration_signal']}  path_score={c['path_score']}  "
            f"(claim={c['claim_type']} novelty={c['self_rated_novelty']} rigor={c['self_rated_rigor']} "
            f"numeric_check={numeric_check_display})  "
            f"tokens_in={c['input_tokens']} tokens_out={c['output_tokens']}  "
            f"at={c['generated_at']}",
            fg=typer.colors.CYAN,
        )
        if c["steering_hint"]:
            typer.echo(f"steering_hint: {c['steering_hint']}")
        typer.echo("PROMPT:")
        typer.echo(_clip(c["prompt_text"], full))
        typer.echo("RESULT:")
        typer.echo(_clip(c["raw_text"], full))
        if c["numeric_check"]:
            typer.echo(f"numeric_check: {c['numeric_check']}")
        invocations = ctx.ledger.list_tool_invocations_by_candidate(c["id"])
        if invocations:
            summary = ", ".join(
                f"{inv['skill_name']}(exit={inv['exit_code']}"
                + (", TIMEOUT" if inv["timed_out"] else "") + ")"
                for inv in invocations
            )
            typer.secho(f"tool_calls [{len(invocations)}]: {summary}", fg=typer.colors.MAGENTA)
            if full:
                for inv in invocations:
                    typer.echo(f"  -> {inv['skill_name']} input: {inv['input_text']}")
                    typer.echo(f"     stdout: {inv['stdout']}")
                    if inv["stderr"]:
                        typer.echo(f"     stderr: {inv['stderr']}")
        if c["parse_status"] == "failed":
            typer.secho(f"parse_error: {c['parse_error']}", fg=typer.colors.RED)
        typer.echo("")
