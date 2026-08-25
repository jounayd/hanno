"""SeedManager: submission, validation, lifecycle, and queue access."""
from __future__ import annotations

from dataclasses import dataclass

from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.models import ReseedEvent, Seed, SeedStatusEvent
from hanno.ledger.repository import Ledger
from hanno.seeds.queue import order_queue
from hanno.seeds.validation import ValidationResult, validate_seed_text

EXHAUSTION_THRESHOLD_CYCLES = 3  # consecutive zero-sieve-pass cycles (Sieve arrives Step 5)


@dataclass
class SubmitResult:
    ok: bool
    seed_id: str | None = None
    feedback: str | None = None
    duplicate_classification: str | None = None  # NOVEL | NEAR_DUPLICATE | EXACT_DUPLICATE
    duplicate_of_seed_id: str | None = None


@dataclass
class ReseedResult:
    ok: bool
    seed_id: str | None = None
    original_seed_id: str | None = None
    chain_depth: int | None = None
    feedback: str | None = None
    duplicate_classification: str | None = None
    duplicate_of_seed_id: str | None = None


class SeedManager:
    def __init__(self, ledger: Ledger, fingerprint_engine: FingerprintEngine):
        self._ledger = ledger
        self._fingerprint_engine = fingerprint_engine

    def submit(
        self, text: str, domain: str, type: str = "human",
        submitter_id: str | None = None, parent_discovery_id: str | None = None,
    ) -> SubmitResult:
        validation: ValidationResult = validate_seed_text(text, domain)
        if not validation.ok:
            return SubmitResult(ok=False, feedback=validation.feedback)

        seed = Seed(
            text=text, domain=domain, type=type,
            submitter_id=submitter_id, parent_discovery_id=parent_discovery_id,
        )

        # Concept-level uniqueness via the Fingerprint Engine (Module 3),
        # scoped across ALL domains, not just the submitted domain — a
        # cross-domain conceptual duplicate is still a duplicate
        # (General-spec.txt:578-583). Per General-spec.txt:343-347, seeds are
        # flagged, never auto-rejected, regardless of classification.
        result = self._fingerprint_engine.process("seed", seed.id, text)
        feedback = None
        if result.classification in ("NEAR_DUPLICATE", "EXACT_DUPLICATE"):
            feedback = (
                f"{result.classification} of existing seed {result.matched_entity_id} "
                f"(similarity={result.similarity_score:.3f}, flagged, not rejected)"
            )

        self._ledger.insert_seed(seed)
        self._ledger.insert_seed_status_event(
            SeedStatusEvent(seed_id=seed.id, status="queued", reason="submitted")
        )
        return SubmitResult(
            ok=True, seed_id=seed.id, feedback=feedback,
            duplicate_classification=result.classification,
            duplicate_of_seed_id=result.matched_entity_id,
        )

    def list_queue(self, domain: str | None = None) -> list[dict]:
        seeds = self._ledger.list_seeds(domain=domain)
        return order_queue(seeds)

    def next_for_domain(self, domain: str) -> dict | None:
        queue = self.list_queue(domain=domain)
        eligible = [s for s in queue if s["status"] in ("queued", "active")]
        return eligible[0] if eligible else None

    def mark_active(self, seed_id: str) -> None:
        self._ledger.insert_seed_status_event(
            SeedStatusEvent(seed_id=seed_id, status="active", reason="cycle started")
        )

    def mark_exhausted_if_needed(self, seed_id: str, consecutive_zero_pass_cycles: int) -> bool:
        if consecutive_zero_pass_cycles >= EXHAUSTION_THRESHOLD_CYCLES:
            self._ledger.insert_seed_status_event(
                SeedStatusEvent(
                    seed_id=seed_id, status="exhausted",
                    reason=f"{consecutive_zero_pass_cycles} consecutive cycles with zero sieve passes",
                )
            )
            return True
        return False

    def retire(self, seed_id: str, reason: str) -> None:
        self._ledger.insert_seed_status_event(
            SeedStatusEvent(seed_id=seed_id, status="retired", reason=reason)
        )

    def _resolve_reseed_chain(self, parent_seed_id: str) -> tuple[str, int]:
        """Walk back through prior reseed_events to find the root seed and depth.

        If parent_seed_id was itself produced by an earlier reseed, its
        original_seed_id/chain_depth are inherited and incremented; otherwise
        parent_seed_id is the root of a brand new chain (depth 1).
        """
        prior = self._ledger.get_reseed_event_by_generated_seed(parent_seed_id)
        if prior is None:
            return parent_seed_id, 1
        return prior["original_seed_id"], prior["chain_depth"] + 1

    def reseed_from_candidate(
        self, candidate_id: str, text: str, domain: str,
        operator_id: str | None = None,
    ) -> ReseedResult:
        candidate = self._ledger.get_candidate(candidate_id)
        if candidate is None:
            return ReseedResult(ok=False, feedback=f"candidate {candidate_id!r} not found")

        parent_seed_id = candidate["seed_id"]
        original_seed_id, chain_depth = self._resolve_reseed_chain(parent_seed_id)

        submit_result = self.submit(text=text, domain=domain, type="derivative")
        if not submit_result.ok:
            return ReseedResult(ok=False, feedback=submit_result.feedback)

        self._ledger.insert_reseed_event(
            ReseedEvent(
                original_seed_id=original_seed_id,
                parent_seed_id=parent_seed_id,
                generated_reseed_id=submit_result.seed_id,
                trigger_type="manual",
                triggering_candidates=[candidate_id],
                chain_depth=chain_depth,
                operator_id=operator_id,
            )
        )
        return ReseedResult(
            ok=True, seed_id=submit_result.seed_id,
            original_seed_id=original_seed_id, chain_depth=chain_depth,
            feedback=submit_result.feedback,
            duplicate_classification=submit_result.duplicate_classification,
            duplicate_of_seed_id=submit_result.duplicate_of_seed_id,
        )
