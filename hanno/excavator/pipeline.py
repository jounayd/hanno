"""ExcavatorPipeline: orchestrates one excavation cycle (Step 3, math only)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from hanno.config import HannoConfig
from hanno.excavator.arm_selector import (
    REASONING_EFFORT_DEEP,
    Arm,
    build_arm_space,
    select_plan,
)
from hanno.excavator.arm_selector import _diagonal_order as _diagonal_temp_variant_order
from hanno.excavator.client import ModelClient
from hanno.excavator.client_factory import build_client
from hanno.excavator.numeric_check import evaluate_numeric_check
from hanno.excavator.parser import parse_candidate_json
from hanno.excavator.prompts import ALL_VARIANTS, STEERING_HINTS, build_prompt, build_refined_seed_text
from hanno.excavator.scoring import compute_path_score
from hanno.excavator.spend_tracker import SpendLimitExceeded, SpendTracker, estimate_cost_usd
from hanno.fingerprint.engine import FingerprintEngine
from hanno.fingerprint.similarity import cosine_similarity
from hanno.ledger.models import (
    ArmScoreEvent, Candidate, Discovery, ExcavationCycle, LeanVerification, SeedStatEvent,
    ToolInvocation, new_id, now_iso,
)
from hanno.ledger.repository import Ledger
from hanno.lean.autoformalize import autoformalize_with_retry
from hanno.lean.checker import classify_result
from hanno.logging_setup import get_logger
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import format_for_model, run_skill

logger = get_logger("excavator.pipeline")

MATH_DOMAIN = "mathematics"


@dataclass
class CycleResult:
    seed_id: str
    candidates_requested: int
    candidates_parsed_ok: int = 0
    candidates_failed: int = 0
    candidates_exact_duplicate: int = 0
    candidates_near_duplicate: int = 0
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    candidate_ids: list[str] = field(default_factory=list)
    halted_on_spend_limit: bool = False
    branch_point_count: int = 0
    dead_end_count: int = 0
    suggested_seed_texts: list[str] = field(default_factory=list)
    reseed_recommended: bool = False
    reseed_reason: str | None = None
    total_path_score: float = 0.0
    scored_count: int = 0

    @property
    def avg_path_score(self) -> float:
        return self.total_path_score / self.scored_count if self.scored_count else 0.0


# "Eligible" for carrying an iterative-mining thread forward: successfully
# parsed (a parse failure has core_claim=None and every other field None,
# which would otherwise vacuously pass every check below), not a rehash of
# something already known, not self-identified as a dead end, and not
# mechanically disproven by its own numeric_check.
def _is_eligible_for_refinement(candidate: dict) -> bool:
    return (
        candidate["parse_status"] == "ok"
        and candidate["duplicate_status"] != "EXACT_DUPLICATE"
        and candidate["exploration_signal"] != "dead_end"
        and candidate["numeric_check_result"] != 0
    )


@dataclass
class BeamThread:
    lineage: list[str] = field(default_factory=list)  # candidate ids, oldest -> newest
    working_seed_text: str = ""
    best_path_score: float = 0.0
    # Consecutive iterations (within THIS lineage specifically) whose best
    # candidate has been >= strong_path_score_threshold -- incremented
    # whether the lineage advanced to a new candidate or was retained at its
    # existing best (see iteration_lineage_retention_threshold). Drives
    # strong-path detection on genuine sustained refinement of one idea,
    # not a coincidence of two different threads each having one good round.
    strong_streak: int = 0


@dataclass
class IterationSummary:
    iteration: int
    cycle_results: list[CycleResult]
    beam_after: list[BeamThread]
    stopped_early: bool = False
    stop_reason: str | None = None


@dataclass
class IterativeMiningResult:
    seed_id: str
    iterations: list[IterationSummary] = field(default_factory=list)
    strong_path_found: bool = False
    strong_path_reason: str | None = None
    best_lineage: list[str] = field(default_factory=list)
    # Set when this run continued a previous run's beam search on the same
    # seed: the highest iteration number found in the ledger at startup. New
    # iterations are numbered from resumed_from_iteration + 1 so the seed's
    # history stays one cumulative sequence across runs.
    resumed_from_iteration: int | None = None
    # Set when until_promising=True and a candidate independently cleared the
    # Lean triviality filter (compiles, not trivially known) -- see
    # run_iterative_mining's in-loop check.
    promising_found: bool = False
    promising_candidate_id: str | None = None
    promising_reason: str | None = None

    @property
    def total_cost_usd(self) -> float:
        return sum(cr.total_cost_usd for it in self.iterations for cr in it.cycle_results)

    @property
    def total_candidates_parsed_ok(self) -> int:
        return sum(cr.candidates_parsed_ok for it in self.iterations for cr in it.cycle_results)

    @property
    def total_input_tokens(self) -> int:
        return sum(cr.total_input_tokens for it in self.iterations for cr in it.cycle_results)

    @property
    def total_output_tokens(self) -> int:
        return sum(cr.total_output_tokens for it in self.iterations for cr in it.cycle_results)


def _round_robin_plan(n_candidates: int, model: str, temperatures: list[float]) -> list[Arm]:
    """Legacy fallback strategy (arm_selection_strategy: round_robin): exhaustive
    coverage with no memory of past performance. Diagonal-ordered (see
    arm_selector._diagonal_order) so even a small --candidates count samples
    more than one temperature."""
    variants = list(ALL_VARIANTS.keys())
    grid = _diagonal_temp_variant_order(temperatures, variants)
    plan = []
    i = 0
    while len(plan) < n_candidates:
        temperature, variant = grid[i % len(grid)]
        plan.append(Arm(model=model, temperature=temperature, prompt_variant=variant, reasoning_effort="standard"))
        i += 1
    return plan[:n_candidates]


class ExcavatorPipeline:
    def __init__(
        self, ledger: Ledger, config: HannoConfig, fingerprint_engine: FingerprintEngine,
        client: ModelClient | None = None, skills: list[SkillDefinition] | None = None,
    ):
        self._ledger = ledger
        self._config = config
        self._fingerprint_engine = fingerprint_engine
        self._skills = skills or []
        self._skills_by_name = {s.name: s for s in self._skills}
        self._client = client or build_client(config, api_key=None)

    def _build_plan(self, n_candidates: int) -> list[Arm]:
        cfg = self._config.excavator
        if cfg.arm_selection_strategy == "round_robin":
            return _round_robin_plan(n_candidates, cfg.model, cfg.temperatures)

        arms = build_arm_space(
            cfg.model, cfg.temperatures, list(ALL_VARIANTS.keys()), cfg.reasoning_efforts,
        )
        arm_stats = self._ledger.get_arm_stats(MATH_DOMAIN)
        return select_plan(n_candidates, arms, arm_stats, cfg.ucb1_exploration_constant)

    def _refresh_already_explored(self, seed_id: str, limit: int) -> list[str]:
        claims = [
            c["core_claim"] for c in self._ledger.list_candidates_by_seed(seed_id) if c["core_claim"]
        ]
        return claims[-limit:]

    async def run_cycle(
        self, seed_id: str, seed_text: str, n_candidates: int,
        iteration: int = 1, refined_from_candidate_id: str | None = None,
    ) -> CycleResult:
        started_at = now_iso()
        cfg = self._config.excavator
        plan = self._build_plan(n_candidates)
        # A second, independent axis from (temperature, variant, reasoning_effort):
        # rotates a distinct strategic angle across candidate SLOTS so a batch
        # doesn't all reach for the same well-known approach (observed in
        # practice on a heavily-studied seed) -- costs nothing extra, no arm
        # bookkeeping, just prompt content.
        hints = [STEERING_HINTS[i % len(STEERING_HINTS)] for i in range(len(plan))]
        spend_tracker = SpendTracker(self._ledger, cfg.daily_spend_limit_usd)
        semaphore = asyncio.Semaphore(cfg.max_parallel_calls if cfg.parallel_execution else 1)
        result = CycleResult(seed_id=seed_id, candidates_requested=n_candidates)

        # Fed into every prompt so candidates diverge from what's already been
        # produced, both in earlier cycles AND (when wave_size > 0) earlier
        # waves of THIS cycle -- reassigned between waves further down.
        already_explored = self._refresh_already_explored(seed_id, cfg.max_prior_claims_in_prompt)

        def _record_arm_score(arm: Arm, candidate_id: str, score: float) -> None:
            self._ledger.insert_arm_score_event(
                ArmScoreEvent(
                    domain=MATH_DOMAIN, model=arm.model, temperature=arm.temperature,
                    prompt_variant=arm.prompt_variant, reasoning_effort=arm.reasoning_effort,
                    candidate_id=candidate_id, score=score,
                )
            )
            result.total_path_score += score
            result.scored_count += 1

        async def process_one(arm: Arm, steering_hint: str) -> None:
            async with semaphore:
                try:
                    spend_tracker.check_before_call()
                except SpendLimitExceeded as e:
                    result.halted_on_spend_limit = True
                    logger.warning(str(e))
                    return

                available_tools = (
                    [(s.name, s.description) for s in self._skills] if self._skills else None
                )
                prompt = build_prompt(
                    seed_text, arm.prompt_variant, already_explored, steering_hint,
                    available_tools=available_tools,
                )
                is_deep = arm.reasoning_effort == REASONING_EFFORT_DEEP
                max_tokens = cfg.deep_reasoning_max_tokens if is_deep else cfg.max_tokens_per_candidate
                thinking_budget = cfg.deep_thinking_budget_tokens if is_deep else None

                # The candidate id is fixed BEFORE generation so tool
                # invocations made mid-generation can reference it; the
                # rows are buffered and inserted right after the candidate
                # (FK order), succeed or fail.
                candidate_id = new_id()
                invocations: list[ToolInvocation] = []

                def tool_executor(name: str, tool_input: dict) -> str:
                    skill = self._skills_by_name.get(name)
                    if skill is None:
                        return f"error: unknown tool {name!r}"
                    input_text = str((tool_input or {}).get("input", ""))
                    res = run_skill(skill, input_text)
                    invocations.append(ToolInvocation(
                        candidate_id=candidate_id, skill_name=name, input_text=input_text,
                        stdout=res.stdout, stderr=res.stderr, exit_code=res.exit_code,
                        duration_ms=res.duration_ms, timed_out=res.timed_out,
                    ))
                    return format_for_model(res)

                def _persist_invocations() -> None:
                    for inv in invocations:
                        self._ledger.insert_tool_invocation(inv)

                generate_kwargs: dict = dict(
                    max_tokens=max_tokens, thinking_budget_tokens=thinking_budget,
                    cache_prefix=ALL_VARIANTS[arm.prompt_variant],
                )
                if self._skills:
                    generate_kwargs.update(
                        tools=self._skills,
                        tool_executor=tool_executor,
                        max_tool_rounds=self._config.skills.max_tool_rounds_per_candidate,
                    )

                try:
                    generation = await self._client.generate(
                        prompt, arm.temperature, **generate_kwargs,
                    )
                except Exception as e:  # noqa: BLE001 — log and record as a failed candidate
                    logger.error(f"generation call failed: {e}")
                    candidate = Candidate(
                        id=candidate_id,
                        seed_id=seed_id, domain=MATH_DOMAIN, temperature=arm.temperature,
                        prompt_variant=arm.prompt_variant, model=arm.model, prompt_text=prompt,
                        raw_text="", reasoning_effort=arm.reasoning_effort, path_score=0.0,
                        steering_hint=steering_hint, iteration=iteration,
                        refined_from_candidate_id=refined_from_candidate_id,
                        parse_status="failed", parse_error=f"API call failed: {e}",
                    )
                    self._ledger.insert_candidate(candidate)
                    _persist_invocations()
                    _record_arm_score(arm, candidate.id, 0.0)
                    result.candidates_failed += 1
                    return

                cost = estimate_cost_usd(generation.input_tokens, generation.output_tokens, cfg.provider)
                spend_tracker.record(cost)
                result.total_cost_usd += cost
                result.total_input_tokens += generation.input_tokens
                result.total_output_tokens += generation.output_tokens

                parsed, error = parse_candidate_json(generation.text)
                if parsed is not None:
                    numeric_check_result = evaluate_numeric_check(parsed.get("numeric_check"))
                    path_score = compute_path_score(
                        self_rated_novelty=parsed["self_rated_novelty"],
                        self_rated_rigor=parsed["self_rated_rigor"],
                        duplicate_status=None,  # duplicate status isn't known until fingerprinting below
                        exploration_signal=parsed.get("exploration_signal"),
                        numeric_check_result=numeric_check_result,
                        claim_type=parsed.get("claim_type"),
                    )
                    candidate = Candidate(
                        id=candidate_id,
                        seed_id=seed_id, domain=parsed.get("domain", MATH_DOMAIN),
                        temperature=arm.temperature, prompt_variant=arm.prompt_variant, model=arm.model,
                        prompt_text=prompt,
                        raw_text=generation.text, core_claim=parsed.get("core_claim"),
                        formal_statement=parsed.get("formal_statement"),
                        intuition=parsed.get("intuition"), connections=parsed.get("connections"),
                        self_assessed_novelty=parsed.get("self_assessed_novelty"),
                        identified_weaknesses=parsed.get("identified_weaknesses"),
                        exploration_signal=parsed.get("exploration_signal"),
                        suggested_seeds=parsed.get("suggested_seeds"),
                        input_tokens=generation.input_tokens,
                        output_tokens=generation.output_tokens,
                        reasoning_effort=arm.reasoning_effort,
                        self_rated_novelty=parsed["self_rated_novelty"],
                        self_rated_rigor=parsed["self_rated_rigor"],
                        path_score=path_score,
                        numeric_check=parsed.get("numeric_check"),
                        numeric_check_result=numeric_check_result,
                        claim_type=parsed.get("claim_type"),
                        steering_hint=steering_hint, iteration=iteration,
                        refined_from_candidate_id=refined_from_candidate_id,
                        parse_status="ok",
                    )
                    result.candidates_parsed_ok += 1

                    if candidate.exploration_signal == "branch_point":
                        result.branch_point_count += 1
                    elif candidate.exploration_signal == "dead_end":
                        result.dead_end_count += 1
                    for s in candidate.suggested_seeds or []:
                        if s not in result.suggested_seed_texts:
                            result.suggested_seed_texts.append(s)

                    # Fingerprint Engine (Module 3, General-spec.txt:112-115):
                    # concept-level dedup against every previously-stored candidate
                    # fingerprint. EXACT_DUPLICATE candidates are still persisted
                    # (Rule 2: failures/duplicates are data) but flagged so
                    # downstream Sieve (Step 5) can exclude them from pipeline.
                    fp_result = self._fingerprint_engine.process(
                        "candidate", candidate.id, candidate.core_claim or candidate.raw_text
                    )
                    candidate.duplicate_status = fp_result.classification
                    if fp_result.classification == "EXACT_DUPLICATE":
                        result.candidates_exact_duplicate += 1
                    elif fp_result.classification == "NEAR_DUPLICATE":
                        result.candidates_near_duplicate += 1

                    # Re-score now that duplicate_status is known — an exact
                    # duplicate is zero-value regardless of self-rated confidence.
                    candidate.path_score = compute_path_score(
                        self_rated_novelty=candidate.self_rated_novelty,
                        self_rated_rigor=candidate.self_rated_rigor,
                        duplicate_status=candidate.duplicate_status,
                        exploration_signal=candidate.exploration_signal,
                        numeric_check_result=numeric_check_result,
                        claim_type=candidate.claim_type,
                    )
                else:
                    candidate = Candidate(
                        id=candidate_id,
                        seed_id=seed_id, domain=MATH_DOMAIN, temperature=arm.temperature,
                        prompt_variant=arm.prompt_variant, model=arm.model, prompt_text=prompt,
                        raw_text=generation.text, reasoning_effort=arm.reasoning_effort, path_score=0.0,
                        steering_hint=steering_hint, iteration=iteration,
                        refined_from_candidate_id=refined_from_candidate_id,
                        input_tokens=generation.input_tokens, output_tokens=generation.output_tokens,
                        parse_status="failed", parse_error=error,
                    )
                    result.candidates_failed += 1

                self._ledger.insert_candidate(candidate)
                _persist_invocations()
                result.candidate_ids.append(candidate.id)
                _record_arm_score(arm, candidate.id, candidate.path_score)

        wave_size = cfg.wave_size if cfg.wave_size > 0 else len(plan)
        for start in range(0, len(plan), wave_size):
            wave_arms = plan[start:start + wave_size]
            wave_hints = hints[start:start + wave_size]
            await asyncio.gather(*(
                process_one(arm, hint) for arm, hint in zip(wave_arms, wave_hints)
            ))
            if start + wave_size < len(plan):
                already_explored = self._refresh_already_explored(seed_id, cfg.max_prior_claims_in_prompt)

        if result.branch_point_count > 0 and result.suggested_seed_texts:
            result.reseed_recommended = True
            result.reseed_reason = (
                f"{result.branch_point_count} candidate(s) identified distinct branching "
                f"directions — review suggested_seed_texts before continuing to mine this seed."
            )
        elif result.candidates_parsed_ok > 0:
            dead_end_ratio = result.dead_end_count / result.candidates_parsed_ok
            if dead_end_ratio >= cfg.dead_end_reseed_threshold:
                result.reseed_recommended = True
                result.reseed_reason = (
                    f"{result.dead_end_count}/{result.candidates_parsed_ok} candidates signaled "
                    f"dead_end (>= {cfg.dead_end_reseed_threshold:.0%} threshold) — this seed's "
                    f"direction appears exhausted, consider a new human-submitted seed."
                )

        self._ledger.insert_excavation_cycle(
            ExcavationCycle(
                seed_id=seed_id, candidates_requested=n_candidates,
                candidates_parsed_ok=result.candidates_parsed_ok,
                candidates_failed=result.candidates_failed,
                total_cost_usd=result.total_cost_usd,
                total_input_tokens=result.total_input_tokens,
                total_output_tokens=result.total_output_tokens,
                iteration=iteration, refined_from_candidate_id=refined_from_candidate_id,
                started_at=started_at, completed_at=now_iso(),
            )
        )
        self._ledger.insert_seed_stat_event(
            SeedStatEvent(
                seed_id=seed_id, cycles_run_delta=1,
                candidates_delta=result.candidates_parsed_ok + result.candidates_failed,
            )
        )
        return result

    def _select_diverse_top(
        self, eligible: list[tuple[BeamThread, dict]], top_k: int, max_similarity: float,
    ) -> list[tuple[BeamThread, dict]]:
        """Greedy maximal-marginal-relevance selection: walk candidates in
        score order, skipping any whose fingerprint is too similar to an
        already-picked beam member. Two rewordings of the same idea can't
        occupy two beam slots; the beam may come back smaller than top_k
        (better to explore one idea than pay to explore the same idea twice).
        Candidates with no stored fingerprint are never skipped for
        similarity — no evidence of redundancy, so don't punish them."""
        if max_similarity >= 1.0 or top_k <= 1:
            return eligible[:top_k]

        vectors = self._ledger.get_full_vectors_for_entities(
            "candidate", [c["id"] for _, c in eligible]
        )
        picked: list[tuple[BeamThread, dict]] = []
        for thread, c in eligible:
            if len(picked) == top_k:
                break
            vec = vectors.get(c["id"])
            too_close = vec is not None and any(
                vectors.get(p["id"]) is not None
                and cosine_similarity(vec, vectors[p["id"]]) >= max_similarity
                for _, p in picked
            )
            if not too_close:
                picked.append((thread, c))
        return picked

    @staticmethod
    def _compute_strong_streak(lineage: list[str], by_id: dict[str, dict], threshold: float) -> int:
        """Walk a lineage backward, counting consecutive trailing candidates
        at/above the strong-path threshold. Used to seed strong_streak when
        resuming a beam across runs -- a conservative approximation, since a
        candidate retained (not refined) for N iterations within a single
        live run only appears once in the stored lineage, so a resumed
        streak can undercount time spent retained in a prior run. That's the
        safe direction: it can only delay STRONG PATH FOUND, never fabricate
        one."""
        streak = 0
        for cid in reversed(lineage):
            c = by_id.get(cid)
            if c is None or c["path_score"] < threshold:
                break
            streak += 1
        return streak

    @staticmethod
    def _reconstruct_lineage(candidate: dict, by_id: dict[str, dict]) -> list[str]:
        """Walk refined_from_candidate_id links back to the root, returning
        candidate ids oldest -> newest (the BeamThread lineage convention),
        including the candidate itself."""
        lineage: list[str] = []
        cur: dict | None = candidate
        while cur is not None:
            lineage.append(cur["id"])
            parent_id = cur.get("refined_from_candidate_id")
            cur = by_id.get(parent_id) if parent_id else None
        lineage.reverse()
        return lineage

    def _resume_state(
        self, seed_id: str, seed_text: str, top_k: int, max_similarity: float,
        strong_path_threshold: float,
    ) -> tuple[list[BeamThread], int, list[float]] | None:
        """Rebuild the beam-search state left behind by previous runs on this
        seed, so a new `hanno mine --depth N` continues the search instead of
        restarting from iteration 1. Returns (beam, last_iteration,
        best_score_per_iteration_history), or None when the seed has never
        been mined (fresh start).

        The beam is reconstructed from the most recent prior iteration that
        still has eligible candidates (the very last one may hold only parse
        failures or dead ends -- walk back rather than give up). If no prior
        candidate anywhere is eligible, the beam falls back to the original
        seed but iteration numbering still continues, keeping the ledger's
        iteration sequence cumulative."""
        prior = self._ledger.list_candidates_by_seed(seed_id)
        if not prior:
            return None

        by_id = {c["id"]: c for c in prior}
        last_iteration = max(c["iteration"] for c in prior)

        by_iteration: dict[int, list[dict]] = {}
        for c in prior:
            by_iteration.setdefault(c["iteration"], []).append(c)

        # Per-iteration best eligible path_score, feeding the plateau and
        # strong-path logic so "sustained progress" is judged across runs.
        history = [
            max(
                (c["path_score"] for c in by_iteration.get(i, []) if _is_eligible_for_refinement(c)),
                default=0.0,
            )
            for i in range(1, last_iteration + 1)
        ]

        for iteration in range(last_iteration, 0, -1):
            eligible = [
                (BeamThread(lineage=self._reconstruct_lineage(c, by_id)), c)
                for c in by_iteration.get(iteration, [])
                if _is_eligible_for_refinement(c)
            ]
            eligible.sort(key=lambda tc: tc[1]["path_score"], reverse=True)
            top = self._select_diverse_top(eligible, top_k, max_similarity)
            if top:
                beam = [
                    BeamThread(
                        lineage=thread.lineage,
                        working_seed_text=build_refined_seed_text(seed_text, c),
                        best_path_score=c["path_score"],
                        strong_streak=self._compute_strong_streak(
                            thread.lineage, by_id, strong_path_threshold
                        ),
                    )
                    for thread, c in top
                ]
                return beam, last_iteration, history

        # Nothing eligible in any prior iteration: continue numbering, but
        # mine the original seed fresh.
        return (
            [BeamThread(lineage=[], working_seed_text=seed_text, best_path_score=0.0)],
            last_iteration,
            history,
        )

    async def _check_promising(
        self, seed_id: str, candidates: list[tuple[BeamThread, dict]], result: IterativeMiningResult,
    ) -> None:
        """Lean-checks every eligible NOVEL candidate from this iteration
        (not just the single beam-selection winner) up to
        max_lean_checks_per_iteration, stopping at the first genuine
        survivor. `candidates` should be this iteration's full eligible
        pool (every thread, before the beam's own top-k diversity cut) --
        NOT just the narrower beam selection -- because self-rated
        path_score is not a trustworthy enough signal to let it gate the
        ENTIRE Lean-check budget down to one candidate: a genuinely good
        idea that merely didn't win that iteration's score comparison
        (against, say, a vague-but-confident-sounding one) would otherwise
        never get its own shot at verification. Records a LeanVerification
        either way for each candidate checked, and a Discovery on a
        survivor. Skips a candidate already checked (a lineage kept alive
        by retention shouldn't be re-checked every round it survives) and
        skips quietly (with a log line) if the local Lean toolchain isn't
        set up -- the CLI's pre-flight check is the real gate, this is just
        defense in depth."""
        cfg = self._config.excavator
        by_id = {c["id"]: c for _, c in candidates}  # de-dupe: same candidate can reach here via >1 thread
        eligible = [
            c for c in by_id.values()
            if c["domain"] == MATH_DOMAIN
            and c["duplicate_status"] == "NOVEL"
            and c["path_score"] >= cfg.strong_path_score_threshold
            and not self._ledger.list_lean_verifications_by_candidate(c["id"])
        ]
        if not eligible:
            return
        eligible.sort(key=lambda c: c["path_score"], reverse=True)

        lean_cfg = self._config.lean_verifier
        project_dir = Path(lean_cfg.mathlib_cache_path)
        if not project_dir.exists():
            logger.warning(
                "until_promising: Lean project directory %s not found, skipping check "
                "for %d eligible candidate(s)", project_dir, len(eligible),
            )
            return

        for candidate in eligible[: cfg.max_lean_checks_per_iteration]:
            outcome = await autoformalize_with_retry(
                candidate, self._client, project_dir,
                lean_cfg.lean_timeout_seconds, lean_cfg.max_translation_attempts,
            )
            label = classify_result(outcome.lean_code, outcome.check)

            self._ledger.insert_lean_verification(LeanVerification(
                candidate_id=candidate["id"],
                lean_code=outcome.lean_code,
                translation_attempt_number=outcome.attempts,
                lean_stdout=outcome.check.stdout if outcome.check else None,
                lean_stderr=outcome.check.stderr if outcome.check else None,
                result=label,
                execution_time_ms=outcome.check.duration_ms if outcome.check else None,
            ))

            if label is not None:
                continue  # not a survivor -- MALFORMED / UNTRANSLATABLE / KNOWN / TIMEOUT, try the next eligible candidate

            result.promising_found = True
            result.promising_candidate_id = candidate["id"]
            result.promising_reason = (
                f"candidate {candidate['id']} cleared the Lean triviality filter "
                f"(compiles, not trivially closed by exact?/simp) — "
                f"path_score={candidate['path_score']:.2f}"
            )
            self._ledger.insert_discovery(Discovery(
                candidate_id=candidate["id"],
                seed_id=seed_id,
                domain=candidate["domain"],
                statement=candidate["core_claim"],
                formal_statement=outcome.lean_code,
                verification_result="SURVIVED",
                lean_verified=True,
                human_reviewed=False,
            ))
            return  # stop at the first survivor this iteration

    async def run_iterative_mining(
        self, seed_id: str, seed_text: str, n_candidates_per_iteration: int, depth: int,
        top_k: int | None = None, resume: bool = True, until_promising: bool = False,
    ) -> IterativeMiningResult:
        """Best-first beam search over the idea space: each iteration expands
        every active thread (a lineage of refined candidates) into its own
        cycle, pools every candidate produced this iteration across all
        threads, and keeps only the global top `top_k` eligible ones — made
        pairwise-diverse via fingerprint similarity — as the next iteration's
        beam. Iterations >= 2 also run one "wildcat" thread mining the
        ORIGINAL seed fresh (when iteration_exploration_thread is on), so the
        beam can never fully tunnel into one sub-topic. depth=1 (the CLI
        default) degenerates to exactly one ordinary run_cycle call.

        until_promising=True adds an early-stop condition on top of depth
        (which still acts as a safety ceiling): after each iteration, the
        single best-scoring novel candidate that clears
        strong_path_score_threshold gets Lean-checked (see
        hanno.lean.autoformalize.autoformalize_with_retry); a genuine
        survivor (compiles, not trivially closed by exact?/simp) stops
        mining immediately and is recorded as a Discovery."""
        cfg = self._config.excavator
        top_k = top_k if top_k is not None else cfg.iteration_top_k
        result = IterativeMiningResult(seed_id=seed_id)
        beam = [BeamThread(lineage=[], working_seed_text=seed_text, best_path_score=0.0)]
        start_iteration = 0
        best_score_per_iteration: list[float] = []
        plateau_streak = 0

        if resume:
            resumed = self._resume_state(
                seed_id, seed_text, top_k, cfg.beam_diversity_max_similarity,
                cfg.strong_path_score_threshold,
            )
            if resumed is not None:
                beam, start_iteration, best_score_per_iteration = resumed
                result.resumed_from_iteration = start_iteration
                logger.info(
                    "resuming iterative mining for seed %s at iteration %d "
                    "(beam of %d thread(s) from previous run(s))",
                    seed_id, start_iteration + 1, len(beam),
                )

        for iteration_num in range(start_iteration + 1, start_iteration + depth + 1):
            threads_to_run = list(beam)
            # The wildcat only makes sense once the beam holds refined
            # threads; on a resume whose fallback beam IS the original seed
            # it would just duplicate that thread.
            if iteration_num >= 2 and cfg.iteration_exploration_thread and any(t.lineage for t in beam):
                # The wildcat thread: a fresh, unrefined pass at the original
                # seed competing for beam slots on equal footing.
                threads_to_run.append(
                    BeamThread(lineage=[], working_seed_text=seed_text, best_path_score=0.0)
                )

            thread_cycle_pairs: list[tuple[BeamThread, CycleResult]] = []
            for thread in threads_to_run:
                parent_id = thread.lineage[-1] if thread.lineage else None
                cycle_result = await self.run_cycle(
                    seed_id, thread.working_seed_text, n_candidates_per_iteration,
                    iteration=iteration_num, refined_from_candidate_id=parent_id,
                )
                thread_cycle_pairs.append((thread, cycle_result))

            all_candidates_by_id = {c["id"]: c for c in self._ledger.list_candidates_by_seed(seed_id)}
            candidates_with_thread = [
                (thread, all_candidates_by_id[cid])
                for thread, cycle_result in thread_cycle_pairs
                for cid in cycle_result.candidate_ids
                if cid in all_candidates_by_id
            ]

            eligible = [
                (thread, c) for thread, c in candidates_with_thread if _is_eligible_for_refinement(c)
            ]

            # Lineage retention: a thread whose best-ever score already clears
            # the retention bar gets its OWN current candidate added back into
            # the pool as a fallback, so it can't be forced out of the beam
            # just because every fresh refinement attempt THIS iteration
            # happened to underperform it. It still competes purely on score
            # against fresh children and the wildcat -- a genuinely better new
            # idea still wins the slot; this only prevents a proven idea from
            # being replaced by a worse one.
            if cfg.iteration_lineage_retention_threshold > 0:
                already_present = {c["id"] for _, c in eligible}
                for thread in beam:
                    if (
                        thread.lineage
                        and thread.best_path_score >= cfg.iteration_lineage_retention_threshold
                        and thread.lineage[-1] not in already_present
                    ):
                        anchor = all_candidates_by_id.get(thread.lineage[-1])
                        if anchor is not None:
                            eligible.append((thread, anchor))

            eligible.sort(key=lambda tc: tc[1]["path_score"], reverse=True)
            top = self._select_diverse_top(eligible, top_k, cfg.beam_diversity_max_similarity)

            if not top:
                result.iterations.append(IterationSummary(
                    iteration=iteration_num,
                    cycle_results=[cr for _, cr in thread_cycle_pairs],
                    beam_after=[],
                    stopped_early=True,
                    stop_reason="no eligible candidate to refine further this iteration",
                ))
                break

            def _next_thread(thread: BeamThread, c: dict) -> BeamThread:
                if thread.lineage and c["id"] == thread.lineage[-1]:
                    # Retention: no refinement beat this lineage's own best
                    # this iteration, so it carries forward unchanged rather
                    # than being replaced by something weaker (or dying
                    # outright if nothing else survived selection) -- but
                    # strong_streak resets, since nothing FRESH reconfirmed
                    # the score this round. "Sustained progress" means
                    # consecutive independently-generated high scores, not
                    # one high score that repeatedly failed to be beaten.
                    return BeamThread(
                        lineage=thread.lineage,
                        working_seed_text=thread.working_seed_text,
                        best_path_score=thread.best_path_score,
                        strong_streak=0,
                    )
                streak = thread.strong_streak + 1 if c["path_score"] >= cfg.strong_path_score_threshold else 0
                return BeamThread(
                    lineage=thread.lineage + [c["id"]],
                    working_seed_text=build_refined_seed_text(seed_text, c),
                    best_path_score=c["path_score"],
                    strong_streak=streak,
                )

            new_beam = [_next_thread(thread, c) for thread, c in top]
            iteration_best = max(t.best_path_score for t in new_beam)
            prior_best = max(best_score_per_iteration, default=0.0)
            plateau_streak = plateau_streak + 1 if iteration_best <= prior_best else 0
            best_score_per_iteration.append(iteration_best)

            if until_promising and not result.promising_found:
                # Deliberately `eligible` (this iteration's full pool, every
                # thread), not `top` (the narrower beam-diversity selection)
                # -- see _check_promising's docstring for why.
                await self._check_promising(seed_id, eligible, result)

            plateaued = (
                cfg.iteration_plateau_patience > 0
                and plateau_streak >= cfg.iteration_plateau_patience
                and iteration_num < start_iteration + depth
            )
            stopped_early = plateaued or result.promising_found
            result.iterations.append(IterationSummary(
                iteration=iteration_num,
                cycle_results=[cr for _, cr in thread_cycle_pairs],
                beam_after=new_beam,
                stopped_early=stopped_early,
                stop_reason=(
                    result.promising_reason if result.promising_found else
                    f"plateau: best score has not improved for {plateau_streak} consecutive "
                    f"iteration(s) — stopping early to avoid spending on a stuck path"
                ) if stopped_early else None,
            ))
            beam = new_beam
            if stopped_early:
                break

        if beam:
            best_thread = max(beam, key=lambda t: t.best_path_score)
            result.best_lineage = best_thread.lineage

            streak_leader = max(beam, key=lambda t: t.strong_streak)
            if streak_leader.strong_streak >= cfg.min_iterations_for_reseed:
                result.strong_path_found = True
                result.strong_path_reason = (
                    f"the SAME refinement lineage has stayed at or above path_score "
                    f"{cfg.strong_path_score_threshold} for {streak_leader.strong_streak} consecutive "
                    f"iteration(s) (current score={streak_leader.best_path_score:.2f}) — this is "
                    f"sustained progress on one idea, not two unrelated threads each having one "
                    f"good round."
                )
                # Point human review at the lineage that actually earned this,
                # which may differ from the single highest raw score in the
                # beam (e.g. a fresh wildcat hit a high score once with no
                # track record, while a steadier lineage sustained a lower
                # score for longer).
                result.best_lineage = streak_leader.lineage

        return result

    async def close(self) -> None:
        await self._client.close()
