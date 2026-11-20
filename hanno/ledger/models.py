"""Append-only ledger records and read-only summaries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class UsageSummary:
    estimated_cost_usd: float
    input_tokens: int
    output_tokens: int
    settled_requests: int
    pending_requests: int
    reserved_cost_usd: float


@dataclass
class Seed:
    text: str
    domain: str
    type: str = "human"
    submitter_id: str | None = None
    parent_discovery_id: str | None = None
    id: str = field(default_factory=new_id)
    submitted_at: str = field(default_factory=now_iso)


@dataclass
class SeedStatusEvent:
    seed_id: str
    status: str
    reason: str | None = None
    id: str = field(default_factory=new_id)
    changed_at: str = field(default_factory=now_iso)


@dataclass
class SeedStatEvent:
    seed_id: str
    cycles_run_delta: int = 0
    candidates_delta: int = 0
    discoveries_delta: int = 0
    id: str = field(default_factory=new_id)
    recorded_at: str = field(default_factory=now_iso)


@dataclass
class Candidate:
    seed_id: str
    domain: str
    temperature: float
    prompt_variant: str
    model: str
    prompt_text: str
    raw_text: str
    parse_status: str  # "ok" | "failed"
    core_claim: str | None = None
    formal_statement: str | None = None
    intuition: str | None = None
    connections: list[str] | None = None
    self_assessed_novelty: str | None = None
    identified_weaknesses: str | None = None
    parse_error: str | None = None
    duplicate_status: str | None = None  # NOVEL | NEAR_DUPLICATE | EXACT_DUPLICATE
    exploration_signal: str | None = None  # continue | branch_point | dead_end
    suggested_seeds: list[str] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_effort: str | None = None  # standard | deep
    self_rated_novelty: float | None = None
    self_rated_rigor: float | None = None
    path_score: float | None = None
    numeric_check: dict | None = None
    numeric_check_result: bool | None = None
    claim_type: str | None = None  # construction | asymptotic | structural
    steering_hint: str | None = None
    iteration: int = 1
    refined_from_candidate_id: str | None = None
    id: str = field(default_factory=new_id)
    generated_at: str = field(default_factory=now_iso)


@dataclass
class ExcavationCycle:
    seed_id: str
    candidates_requested: int
    candidates_parsed_ok: int
    candidates_failed: int
    total_cost_usd: float
    started_at: str
    completed_at: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    iteration: int = 1
    refined_from_candidate_id: str | None = None
    id: str = field(default_factory=new_id)
    usage_accounted: bool = False


@dataclass
class ToolInvocation:
    candidate_id: str
    skill_name: str
    input_text: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False
    id: str = field(default_factory=new_id)
    invoked_at: str = field(default_factory=now_iso)
    phase: str = "generation"


@dataclass
class ArmScoreEvent:
    domain: str
    model: str
    temperature: float
    prompt_variant: str
    reasoning_effort: str
    candidate_id: str
    score: float
    id: str = field(default_factory=new_id)
    recorded_at: str = field(default_factory=now_iso)


@dataclass
class CalibrationEvent:
    module: str
    parameter: str
    reason: str
    old_value: str | None = None
    new_value: str | None = None
    supporting_data: str | None = None
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)


@dataclass
class ReseedEvent:
    original_seed_id: str
    parent_seed_id: str
    generated_reseed_id: str
    trigger_type: str
    chain_depth: int
    trigger_conditions: str | None = None
    triggering_candidates: list[str] | None = None
    operator_id: str | None = None
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)


@dataclass
class LeanVerification:
    candidate_id: str
    lean_code: str | None = None
    translation_confidence: float | None = None
    translation_attempt_number: int | None = None
    lean_stdout: str | None = None
    lean_stderr: str | None = None
    result: str | None = None  # VERIFIED | KNOWN | UNTRANSLATABLE | MALFORMED | TIMEOUT | None
    execution_time_ms: int | None = None
    id: str = field(default_factory=new_id)
    verified_at: str = field(default_factory=now_iso)


@dataclass
class Discovery:
    candidate_id: str
    seed_id: str
    domain: str
    fingerprint_hash: str | None = None
    tier: str | None = None
    statement: str | None = None
    formal_statement: str | None = None
    sieve_score: float | None = None
    verification_result: str | None = None
    lean_verified: bool | None = None
    human_reviewed: bool = False
    human_reviewer_id: str | None = None
    human_verdict: str | None = None  # NOVEL | KNOWN | UNCERTAIN | REJECTED
    human_notes: str | None = None
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
