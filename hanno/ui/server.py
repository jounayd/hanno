"""FastAPI app for `hanno ui`: miner/skill settings, and a live view of a
seed's mining trace. This wraps the same Ledger / ExcavatorPipeline /
SkillDefinition classes the CLI uses -- no separate mining logic, and no
changes to ExcavatorPipeline internals. "Real time" progress is polling:
the frontend re-fetches /api/seeds/{id}/trace every ~1.5s while a job is
running, the same JSON shape `hanno trace --json` produces (see
hanno/ledger/export.py), rendered by the same mind-map viewer ported from
hanno/viz/trace_mindmap.html.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from hanno.config import HannoConfig, diff_and_log_calibration, load_config
from hanno.excavator.client_factory import build_client
from hanno.excavator.pipeline import ExcavatorPipeline
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.export import build_trace_export
from hanno.ledger.repository import Ledger
from hanno.logging_setup import setup_logging
from hanno.seeds.manager import SeedManager
from hanno.skills.registry import find_skill_file, load_skill_files, load_skills, set_skill_enabled
from hanno.ui.jobs import JobRegistry
from hanno.ui.secrets import api_key_status, load_api_key, load_config_overlay, save_api_key, save_config_overlay

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Mirrors hanno/cli/commands/seeds.py's VALID_SEED_TYPES -- kept in sync by
# hand since it's a 3-item tuple, not worth a shared-constants module for.
VALID_SEED_TYPES = ("human", "ai-generated", "derivative")
VALID_SEED_DOMAINS = ("mathematics", "science", "philosophy", "cross-domain")

# Allowlist, not the whole ExcavatorConfig -- mirrors the MINDMAP_CANDIDATE_FIELDS
# pattern in hanno/ledger/export.py: an explicit, documented surface rather
# than exposing every internal knob to a web form.
EXPOSED_EXCAVATOR_FIELDS = [
    "provider", "base_url", "model", "request_timeout_seconds", "daily_spend_limit_usd", "candidates_per_cycle_per_seed", "temperatures",
    "max_tokens_per_candidate", "arm_selection_strategy", "ucb1_exploration_constant",
    "reasoning_efforts", "iteration_top_k", "strong_path_score_threshold",
    "min_iterations_for_reseed", "beam_diversity_max_similarity",
    "iteration_exploration_thread", "iteration_plateau_patience",
    "iteration_lineage_retention_threshold", "max_lean_checks_per_iteration",
]


class AppState:
    """Long-lived, expensive-to-build objects: one Ledger connection and one
    FingerprintEngine (which loads an embedding model) for the server's
    lifetime. HannoConfig itself is reloaded fresh wherever a request needs
    current settings, so Settings-tab saves take effect without a restart."""

    def __init__(self) -> None:
        config = load_config()
        setup_logging(config.logging)
        self.ledger = Ledger.connect(config.ledger.database_path)
        self.ledger.init_schema()
        diff_and_log_calibration(self.ledger, config)
        self.fingerprint_engine = FingerprintEngine(self.ledger, config.fingerprint)
        self.jobs = JobRegistry()


_state: AppState | None = None


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("app state not initialized (server not started via lifespan)")
    return _state


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _state
    _state = AppState()
    yield
    _state.ledger.close()


app = FastAPI(title="Hanno UI", lifespan=lifespan)


# ---------- Seeds ----------

@app.get("/api/seeds")
async def list_seeds(domain: str | None = None) -> list[dict]:
    s = get_state()
    seeds = SeedManager(s.ledger, s.fingerprint_engine).list_queue(domain=domain)
    for seed in seeds:
        # Cheap in-memory check against the job registry -- lets the Seeds
        # tab show "mining now" without a per-row status round-trip.
        seed["mining"] = s.jobs.is_running(seed["id"])
    return seeds


class SeedSubmitRequest(BaseModel):
    text: str
    domain: str
    type: str = "human"
    submitter_id: str | None = None


@app.post("/api/seeds")
async def submit_seed(req: SeedSubmitRequest) -> dict:
    if req.type not in VALID_SEED_TYPES:
        raise HTTPException(400, f"invalid type {req.type!r}. Must be one of: {', '.join(VALID_SEED_TYPES)}")
    if req.domain not in VALID_SEED_DOMAINS:
        raise HTTPException(400, f"invalid domain {req.domain!r}. Must be one of: {', '.join(VALID_SEED_DOMAINS)}")

    s = get_state()
    result = SeedManager(s.ledger, s.fingerprint_engine).submit(
        text=req.text, domain=req.domain, type=req.type, submitter_id=req.submitter_id,
    )
    if not result.ok:
        raise HTTPException(400, result.feedback)
    return {
        "ok": True,
        "seed_id": result.seed_id,
        "feedback": result.feedback,
        "duplicate_classification": result.duplicate_classification,
        "duplicate_of_seed_id": result.duplicate_of_seed_id,
    }


class ReseedRequest(BaseModel):
    candidate_id: str
    text: str
    domain: str
    operator_id: str | None = None


@app.post("/api/reseed")
async def reseed_from_candidate(req: ReseedRequest) -> dict:
    if req.domain not in VALID_SEED_DOMAINS:
        raise HTTPException(400, f"invalid domain {req.domain!r}. Must be one of: {', '.join(VALID_SEED_DOMAINS)}")

    s = get_state()
    result = SeedManager(s.ledger, s.fingerprint_engine).reseed_from_candidate(
        candidate_id=req.candidate_id, text=req.text, domain=req.domain, operator_id=req.operator_id,
    )
    if not result.ok:
        raise HTTPException(400, result.feedback)
    return {
        "ok": True,
        "seed_id": result.seed_id,
        "original_seed_id": result.original_seed_id,
        "chain_depth": result.chain_depth,
        "feedback": result.feedback,
        "duplicate_classification": result.duplicate_classification,
        "duplicate_of_seed_id": result.duplicate_of_seed_id,
    }


# ---------- Trace (live during mining, and post-hoc -- same endpoint) ----------

@app.get("/api/seeds/{seed_id}/trace")
async def get_trace(seed_id: str) -> dict:
    s = get_state()
    seed_row = s.ledger.get_seed(seed_id)
    if seed_row is None:
        raise HTTPException(404, f"seed {seed_id} not found")
    status = s.ledger.get_seed_current_status(seed_id)
    candidates = s.ledger.list_candidates_by_seed(seed_id)
    return build_trace_export(dict(seed_row), status, candidates, s.ledger)


# ---------- Mining jobs ----------

class MineRequest(BaseModel):
    seed_id: str
    candidates: int = 5
    depth: int = 1
    top_k: int | None = None
    fresh: bool = False


async def _run_mining_job(pipeline: ExcavatorPipeline, req: MineRequest, seed_text: str) -> None:
    try:
        if req.depth <= 1:
            await pipeline.run_cycle(req.seed_id, seed_text, req.candidates)
        else:
            await pipeline.run_iterative_mining(
                req.seed_id, seed_text, req.candidates, req.depth,
                top_k=req.top_k, resume=not req.fresh,
            )
    finally:
        await pipeline.close()


@app.post("/api/mine")
async def start_mining(req: MineRequest) -> dict:
    s = get_state()
    if req.depth < 1:
        raise HTTPException(400, "depth must be >= 1")
    if s.jobs.is_running(req.seed_id):
        raise HTTPException(409, f"a mining job is already running for seed {req.seed_id}")

    seed_row = s.ledger.get_seed(req.seed_id)
    if seed_row is None:
        raise HTTPException(404, f"seed {req.seed_id} not found")
    if seed_row["domain"] != "mathematics":
        raise HTTPException(400, "only the mathematics domain is implemented in Step 3")

    config = load_config()
    provider = config.excavator.provider
    # Only the anthropic provider requires a key -- self-hosted/openai_compatible
    # backends (e.g. a local Ollama server) typically don't check auth at all.
    api_key = load_api_key(provider) or (os.environ.get("ANTHROPIC_API_KEY") if provider == "anthropic" else None)
    if provider == "anthropic" and not api_key:
        raise HTTPException(400, "No API key configured. Set one in the Settings tab first.")

    skills = load_skills(config.skills.directory) if config.skills.enabled else []
    client = build_client(config, api_key=api_key)
    pipeline = ExcavatorPipeline(s.ledger, config, s.fingerprint_engine, client=client, skills=skills)

    s.jobs.start(req.seed_id, _run_mining_job(pipeline, req, seed_row["text"]))
    return {"started": True, "seed_id": req.seed_id}


@app.get("/api/mine/{seed_id}/status")
async def mining_status(seed_id: str) -> dict:
    return get_state().jobs.status(seed_id)


# ---------- Config ----------

@app.get("/api/config")
async def get_config() -> dict:
    excavator = load_config().excavator.model_dump()
    return {k: excavator[k] for k in EXPOSED_EXCAVATOR_FIELDS if k in excavator}


@app.post("/api/config")
async def update_config(updates: dict[str, Any]) -> dict:
    unknown = set(updates) - set(EXPOSED_EXCAVATOR_FIELDS)
    if unknown:
        raise HTTPException(400, f"unknown or unexposed config field(s): {sorted(unknown)}")

    # Validate the fully-resolved config WITH these updates applied before
    # writing anything to disk -- an invalid save must never corrupt
    # config/local.yaml or silently break the next `hanno` invocation.
    merged_dump = load_config().model_dump()
    merged_dump["excavator"].update(updates)
    try:
        HannoConfig(**merged_dump)
    except ValidationError as e:
        raise HTTPException(400, f"invalid config: {e}") from e

    save_config_overlay({"excavator": updates})
    return {"saved": True, "overlay": load_config_overlay()}


# ---------- Secrets (API key) ----------

@app.get("/api/secrets/api-key-status")
async def get_api_key_status(provider: str = "anthropic") -> dict:
    return api_key_status(provider)


class ApiKeyRequest(BaseModel):
    api_key: str
    provider: str = "anthropic"


@app.post("/api/secrets/api-key")
async def set_api_key(req: ApiKeyRequest) -> dict:
    if not req.api_key.strip():
        raise HTTPException(400, "api_key must not be empty")
    save_api_key(req.provider, req.api_key.strip())
    return api_key_status(req.provider)


# ---------- Skills ----------

@app.get("/api/skills")
async def list_skills() -> list[dict]:
    config = load_config()
    skills = load_skill_files(config.skills.directory)
    return [
        {
            "name": sk.name, "description": sk.description, "command": sk.command,
            "input_mode": sk.input_mode, "timeout_seconds": sk.timeout_seconds,
            "max_output_chars": sk.max_output_chars, "enabled": sk.enabled,
        }
        for sk in skills
    ]


class SkillToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/skills/{name}/toggle")
async def toggle_skill(name: str, req: SkillToggleRequest) -> dict:
    config = load_config()
    path = find_skill_file(config.skills.directory, name)
    if path is None:
        raise HTTPException(404, f"skill {name!r} not found")
    set_skill_enabled(path, req.enabled)
    return {"name": name, "enabled": req.enabled}


# ---------- Static SPA (must be mounted last so /api/* above takes priority) ----------

class NoCacheStaticFiles(StaticFiles):
    """Forces revalidation on every request. Without this, browsers apply
    heuristic caching to app.js/mindmap.js/app.css (no explicit Cache-Control
    from the default StaticFiles response) and can silently keep serving a
    stale copy after an edit -- even on a normal reload, not just once."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
