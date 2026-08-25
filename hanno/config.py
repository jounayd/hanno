"""Configuration loading for Project Hanno.

Loads config/default.yaml, applies environment variable overrides, and
(when a Ledger is supplied) diffs the resolved config against the last
stored snapshot, logging one calibration_event per changed parameter.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExcavatorConfig(BaseModel):
    candidates_per_cycle_per_seed: int = 50
    temperatures: list[float] = [0.3, 0.6, 0.8, 1.0]
    max_tokens_per_candidate: int = 8000  # raised 2000->4000->8000: deep-iteration context growth plus tool-assisted investigation both push response length past smaller caps, truncating mid-JSON and failing to parse
    domain_specific_variants_per_domain: int = 3
    parallel_execution: bool = True
    max_parallel_calls: int = 10
    daily_spend_limit_usd: float = 25.0
    provider: str = "anthropic"  # "anthropic" | "openai_compatible" -- see hanno/excavator/client_factory.py. openai_compatible covers OpenAI itself plus any self-hosted/hosted server speaking the same wire format (Ollama, LM Studio, vLLM, Groq, ...)
    base_url: str | None = None  # only used for provider=openai_compatible, e.g. "http://localhost:11434/v1" for a local Ollama server
    prover_provider: str | None = None  # optional per-role override (Migration Plan Phase 6): a separate, possibly stronger/dedicated model just for the Prover role. None means "reuse the main provider/model/base_url for proving too" -- fully backward compatible.
    prover_model: str | None = None  # setting this is what actually activates a separate prover client (see build_prover_client) -- prover_provider/prover_base_url alone do nothing without it
    prover_base_url: str | None = None  # only used when prover_provider=openai_compatible; falls back to base_url if unset
    model: str = "claude-sonnet-4-5-20250929"
    request_timeout_seconds: float = 600.0  # per-call HTTP read timeout. Hosted APIs finish well inside this; self-hosted local models (slow tok/s, plus max_tokens_per_candidate/deep_reasoning_max_tokens both well above 600s worth of tokens at local speeds) need this raised or every long response gets killed mid-generation -- see LOCAL_MODEL_SETUP.md
    max_translation_retries: int = 3
    dead_end_reseed_threshold: float = 0.6  # fraction of dead_end signals that triggers a reseed recommendation
    arm_selection_strategy: str = "ucb1"  # "ucb1" | "round_robin"
    ucb1_exploration_constant: float = 1.4
    reasoning_efforts: list[str] = ["standard", "deep"]
    deep_thinking_budget_tokens: int = 4096  # extended-thinking token budget for "deep" arms
    deep_reasoning_max_tokens: int = 16000  # must exceed deep_thinking_budget_tokens
    max_prior_claims_in_prompt: int = 15  # prior core_claims fed back in so candidates diverge instead of reconverging
    wave_size: int = 0  # 0 = disabled (full parallel, current speed). >0 = stage the cycle into sequential waves of this size, feeding each wave's own core_claims into already_explored for the next wave -- trades latency for real intra-cycle diversity.
    iteration_top_k: int = 2  # beam width for iterative mining (hanno mine --depth): how many top candidates carry forward into the next iteration
    strong_path_score_threshold: float = 0.6  # best-of-beam path_score that must be sustained for a "strong path" reseed recommendation
    min_iterations_for_reseed: int = 2  # consecutive iterations that must clear strong_path_score_threshold before recommending human reseed
    beam_diversity_max_similarity: float = 0.85  # beam members must be pairwise below this cosine similarity; 1.0 disables the diversity filter
    iteration_exploration_thread: bool = True  # each iteration >= 2 also mines the ORIGINAL seed fresh (the "wildcat" thread), competing for beam slots -- prevents the beam from tunneling into one sub-topic, at ~+1 thread of cost per iteration
    iteration_plateau_patience: int = 0  # stop iterating early after this many consecutive iterations without a new best score; 0 disables
    iteration_lineage_retention_threshold: float = 0.5  # a lineage whose best-ever path_score clears this bar is never dropped from the beam just because THIS iteration's refinement of it underperformed -- its own best candidate stays eligible as a fallback next iteration too, so a promising idea gets repeated attempts to improve instead of being replaced by a weaker one. 0 disables (old behavior: only this iteration's fresh children compete).
    max_lean_checks_per_iteration: int = 3  # --until-promising: how many of this iteration's eligible (NOVEL, score>=strong_path_score_threshold) candidates get Lean-checked, not just the single top-scoring one -- self-rated path_score is not trustworthy enough on its own to gate the entire check budget down to one winner per iteration; this widens the shot without checking literally everything.

    @field_validator("temperatures")
    @classmethod
    def _temperatures_must_be_api_valid(cls, value: list[float]) -> list[float]:
        # The Anthropic Messages API rejects temperature outside 0.0-1.0 with
        # a 400 — fail at config load time, not mid-cycle after burning calls
        # on every arm that comes before the invalid one.
        out_of_range = [t for t in value if not (0.0 <= t <= 1.0)]
        if out_of_range:
            raise ValueError(
                f"excavator.temperatures contains {out_of_range}, but the Anthropic API "
                f"requires temperature in the range 0.0-1.0"
            )
        return value


class SkillsConfig(BaseModel):
    enabled: bool = True
    directory: str = "skills"  # each *.yaml here defines one miner-registered tool
    max_tool_rounds_per_candidate: int = 6  # cap on model->tool->model round-trips per candidate (each round re-bills the conversation); real runs hit the old cap of 3 mid-investigation


class FingerprintConfig(BaseModel):
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    compressed_dimensions: int = 128
    similarity_threshold_near_duplicate: float = 0.92
    similarity_threshold_exact_duplicate: float = 0.99
    calibration_minimum_fingerprints: int = 500
    recalibration_interval: int = 1000


class SieveConfig(BaseModel):
    coherence_weight: float = 0.25
    novelty_weight: float = 0.25
    significance_weight: float = 0.25
    fertility_weight: float = 0.15
    formalizability_weight: float = 0.10
    pass_threshold: float = 0.65
    coherence_minimum: float = 0.50
    novelty_minimum: float = 0.30
    literature_databases: list[str] = ["arxiv", "semantic_scholar"]


class LeanVerifierConfig(BaseModel):
    lean_binary_path: str = "lean"
    lean_timeout_seconds: int = 120
    max_translation_attempts: int = 3
    max_proof_attempts: int = 5  # separate, larger budget for the deep-verification proof-search loop (hanno/lean/prove.py) -- a real proof is a harder ask than a statement translation
    use_server_mode: bool = True
    mathlib_cache_path: str = "data/mathlib_cache"


class LedgerConfig(BaseModel):
    database_path: str = "data/hanno.db"
    backup_enabled: bool = True
    backup_interval_hours: int = 24
    export_path: str = "data/exports/"


class PipelineConfig(BaseModel):
    domains_enabled: list[str] = ["mathematics"]
    human_review_required_before_public_claim: bool = True


class LoggingConfig(BaseModel):
    log_dir: str = "logs"
    log_file: str = "hanno.log"
    level: str = "INFO"


class HannoConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANNO_", env_nested_delimiter="__", extra="ignore"
    )

    excavator: ExcavatorConfig = ExcavatorConfig()
    skills: SkillsConfig = SkillsConfig()
    fingerprint: FingerprintConfig = FingerprintConfig()
    sieve: SieveConfig = SieveConfig()
    lean_verifier: LeanVerifierConfig = LeanVerifierConfig()
    ledger: LedgerConfig = LedgerConfig()
    pipeline: PipelineConfig = PipelineConfig()
    logging: LoggingConfig = LoggingConfig()


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
LOCAL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "local.yaml"


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(
    path: Path | str = DEFAULT_CONFIG_PATH, local_path: Path | str = LOCAL_CONFIG_PATH,
) -> HannoConfig:
    """Load YAML config, merging an optional local overlay in first.

    `local_path` (config/local.yaml, gitignored) holds only the parameters
    the Settings UI has changed, so the hand-documented, checked-in
    default.yaml is never rewritten. It's deep-merged into the same `raw`
    dict default.yaml produces, so local.yaml wins over default.yaml
    leaf-for-leaf.

    Env var (HANNO_*) precedence, pre-existing and unrelated to this
    overlay: `HannoConfig(**raw)` passes each top-level section (e.g.
    "excavator") as an explicit init value whenever `raw` contains it, and
    pydantic-settings' init source outranks its env source per *field*, not
    per *leaf* -- so a HANNO_EXCAVATOR__* env var only takes effect for a
    seed's excavator settings when `raw` has no "excavator" key at all
    (i.e. default.yaml and local.yaml both omit it entirely). In today's
    config/default.yaml, which populates every section, HANNO_* overrides
    for existing fields are effectively inert. Not fixed here -- flagged as
    a separate pre-existing issue.
    """
    path = Path(path)
    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    local_path = Path(local_path)
    if local_path.exists():
        with open(local_path) as f:
            local_raw = yaml.safe_load(f) or {}
        raw = deep_merge(raw, local_raw)

    return HannoConfig(**raw)


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def diff_and_log_calibration(ledger, config: HannoConfig, reason: str = "config file change") -> int:
    """Compare `config` against the last stored snapshot in the ledger.

    Logs one calibration_event per changed leaf parameter, then stores the
    new snapshot. Returns the number of changes logged. Safe to call on
    every startup — a no-op after the first run if nothing changed.
    """
    new_flat = _flatten(config.model_dump())
    last_snapshot = ledger.get_latest_config_snapshot()

    if last_snapshot is None:
        # Nothing to diff against on the very first run — establish the
        # baseline snapshot silently, do not log 38 "changes from nothing".
        ledger.insert_config_snapshot(json.dumps(config.model_dump()))
        return 0

    old_flat = _flatten(json.loads(last_snapshot))

    changes = 0
    for key, new_value in new_flat.items():
        old_value = old_flat.get(key)
        if old_value != new_value:
            ledger.insert_calibration_event(
                module=key.split(".")[0],
                parameter=key,
                old_value=json.dumps(old_value),
                new_value=json.dumps(new_value),
                reason=reason,
                supporting_data=None,
            )
            changes += 1

    if changes > 0:
        ledger.insert_config_snapshot(json.dumps(config.model_dump()))

    return changes
