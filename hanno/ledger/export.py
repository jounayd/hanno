"""Shared trace-export payload: the exact JSON shape consumed by
hanno/ui/static/mindmap.js (ported from hanno/viz/trace_mindmap.html).
Used by both `hanno trace --json` (hanno/cli/commands/trace.py) and the
live web UI's `/api/seeds/{id}/trace` endpoint, so the two surfaces can
never drift out of sync with each other.
"""
from __future__ import annotations

import json as json_module

# Columns handed to the mind-map viewer. Kept as an explicit allowlist, not
# `dict(row)` wholesale, so the JSON export has a stable, documented shape
# independent of internal ledger column additions.
MINDMAP_CANDIDATE_FIELDS = [
    "id", "iteration", "refined_from_candidate_id", "prompt_variant", "temperature",
    "reasoning_effort", "steering_hint", "parse_status", "parse_error",
    "core_claim", "formal_statement", "intuition", "connections", "identified_weaknesses",
    "duplicate_status", "exploration_signal", "claim_type", "numeric_check",
    "numeric_check_result", "path_score", "self_rated_novelty", "self_rated_rigor",
    "prompt_text", "raw_text", "input_tokens", "output_tokens", "generated_at",
    "suggested_seeds",
]


def _json_field(raw: str | None) -> object:
    if not raw:
        return None
    try:
        return json_module.loads(raw)
    except (TypeError, ValueError):
        return raw


def candidate_for_export(c: dict) -> dict:
    out = {}
    for field in MINDMAP_CANDIDATE_FIELDS:
        value = c[field]
        if field in ("connections", "suggested_seeds", "numeric_check"):
            value = _json_field(value)
        elif field == "numeric_check_result":
            # SQLite stores this as 0/1 -- export real JSON booleans so a JS
            # consumer's `=== true` / `=== false` checks work as written.
            value = None if value is None else bool(value)
        out[field] = value
    return out


def build_trace_export(seed_row: dict, status: str, candidates: list[dict], ledger) -> dict:
    """The full JSON payload for one seed's mining trace: seed metadata plus
    every candidate (with its tool invocations, Lean triviality checks, and
    discovery/deep-verification status), in the shape mindmap.js expects."""
    # list_discoveries() is system-wide (not seed-scoped), so build the
    # lookup once here rather than re-querying it per candidate.
    discoveries_by_candidate = {d["candidate_id"]: d for d in ledger.list_discoveries()}

    exported = []
    for c in candidates:
        entry = candidate_for_export(c)
        entry["tool_invocations"] = [
            {
                "skill_name": inv["skill_name"], "input_text": inv["input_text"],
                "stdout": inv["stdout"], "stderr": inv["stderr"],
                "exit_code": inv["exit_code"], "duration_ms": inv["duration_ms"],
                "timed_out": bool(inv["timed_out"]), "invoked_at": inv["invoked_at"],
            }
            for inv in ledger.list_tool_invocations_by_candidate(c["id"])
        ]
        entry["lean_verifications"] = [
            {
                "result": lv["result"], "lean_code": lv["lean_code"],
                "translation_attempt_number": lv["translation_attempt_number"],
                "lean_stdout": lv["lean_stdout"], "lean_stderr": lv["lean_stderr"],
                "execution_time_ms": lv["execution_time_ms"], "verified_at": lv["verified_at"],
            }
            for lv in ledger.list_lean_verifications_by_candidate(c["id"])
        ]
        d = discoveries_by_candidate.get(c["id"])
        entry["discovery"] = None if d is None else {
            "verification_result": d["verification_result"],
            "lean_verified": None if d["lean_verified"] is None else bool(d["lean_verified"]),
            "human_reviewed": bool(d["human_reviewed"]),
            "human_verdict": d["human_verdict"],
            "human_notes": d["human_notes"],
            "created_at": d["created_at"],
        }
        exported.append(entry)
    return {
        "seed": {
            "id": seed_row["id"], "text": seed_row["text"], "domain": seed_row["domain"],
            "type": seed_row["type"], "status": status,
            "submitted_at": seed_row["submitted_at"], "submitter_id": seed_row["submitter_id"],
        },
        "candidates": exported,
    }
