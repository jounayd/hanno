"""The Ledger repository: insert/read-only interface over the SQLite schema.

Deliberately no update_* or delete_* method exists anywhere in this class.
That is the application-layer half of append-only enforcement (the other
half is the DB triggers in schema.sql).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hanno.ledger import db as db_module
from hanno.ledger.models import (
    ArmScoreEvent,
    Candidate,
    Discovery,
    ExcavationCycle,
    LeanVerification,
    ReseedEvent,
    Seed,
    SeedStatEvent,
    SeedStatusEvent,
    ToolInvocation,
    UsageSummary,
    now_iso,
)


class Ledger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, db_path: str | Path) -> Ledger:
        conn = db_module.connect(db_path)
        return cls(conn)

    def init_schema(self) -> None:
        db_module.init_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Seeds
    # ------------------------------------------------------------------

    def insert_seed(self, seed: Seed) -> None:
        self._conn.execute(
            """INSERT INTO seeds
               (id, text, type, domain, submitter_id, parent_discovery_id, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                seed.id,
                seed.text,
                seed.type,
                seed.domain,
                seed.submitter_id,
                seed.parent_discovery_id,
                seed.submitted_at,
            ),
        )

    def insert_seed_status_event(self, event: SeedStatusEvent) -> None:
        self._conn.execute(
            """INSERT INTO seed_status_events (id, seed_id, status, reason, changed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event.id, event.seed_id, event.status, event.reason, event.changed_at),
        )

    def insert_seed_stat_event(self, event: SeedStatEvent) -> None:
        self._conn.execute(
            """INSERT INTO seed_stat_events
               (id, seed_id, cycles_run_delta, candidates_delta, discoveries_delta, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.seed_id,
                event.cycles_run_delta,
                event.candidates_delta,
                event.discoveries_delta,
                event.recorded_at,
            ),
        )

    def get_seed(self, seed_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM seeds WHERE id = ?", (seed_id,)).fetchone()

    def get_seed_current_status(self, seed_id: str) -> str:
        row = self._conn.execute(
            """SELECT status FROM seed_status_events
               WHERE seed_id = ? ORDER BY changed_at DESC LIMIT 1""",
            (seed_id,),
        ).fetchone()
        return row["status"] if row else "queued"

    def get_seed_stats(self, seed_id: str) -> dict:
        row = self._conn.execute(
            """SELECT COALESCE(SUM(cycles_run_delta), 0) AS cycles_run,
                      COALESCE(SUM(candidates_delta), 0) AS candidates_produced,
                      COALESCE(SUM(discoveries_delta), 0) AS discoveries_attributed
               FROM seed_stat_events WHERE seed_id = ?""",
            (seed_id,),
        ).fetchone()
        return dict(row)

    def list_seeds(self, domain: str | None = None) -> list[dict]:
        query = "SELECT * FROM seeds"
        params: tuple = ()
        if domain:
            query += " WHERE domain = ?"
            params = (domain,)
        query += " ORDER BY submitted_at ASC"
        rows = self._conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["status"] = self.get_seed_current_status(d["id"])
            d.update(self.get_seed_stats(d["id"]))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Candidates & Excavation Cycles
    # ------------------------------------------------------------------

    def insert_candidate(self, candidate: Candidate) -> None:
        self._conn.execute(
            """INSERT INTO candidates
               (id, seed_id, domain, temperature, prompt_variant, model, prompt_text, raw_text,
                core_claim, formal_statement, intuition, connections,
                self_assessed_novelty, identified_weaknesses, parse_status,
                parse_error, duplicate_status, exploration_signal, suggested_seeds,
                input_tokens, output_tokens, reasoning_effort, self_rated_novelty,
                self_rated_rigor, path_score, numeric_check, numeric_check_result,
                claim_type, steering_hint, iteration, refined_from_candidate_id, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.id,
                candidate.seed_id,
                candidate.domain,
                candidate.temperature,
                candidate.prompt_variant,
                candidate.model,
                candidate.prompt_text,
                candidate.raw_text,
                candidate.core_claim,
                candidate.formal_statement,
                candidate.intuition,
                json.dumps(candidate.connections) if candidate.connections else None,
                candidate.self_assessed_novelty,
                candidate.identified_weaknesses,
                candidate.parse_status,
                candidate.parse_error,
                candidate.duplicate_status,
                candidate.exploration_signal,
                json.dumps(candidate.suggested_seeds) if candidate.suggested_seeds else None,
                candidate.input_tokens,
                candidate.output_tokens,
                candidate.reasoning_effort,
                candidate.self_rated_novelty,
                candidate.self_rated_rigor,
                candidate.path_score,
                json.dumps(candidate.numeric_check) if candidate.numeric_check else None,
                None
                if candidate.numeric_check_result is None
                else int(candidate.numeric_check_result),
                candidate.claim_type,
                candidate.steering_hint,
                candidate.iteration,
                candidate.refined_from_candidate_id,
                candidate.generated_at,
            ),
        )

    def get_candidate(self, candidate_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_candidates_by_seed(self, seed_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE seed_id = ? ORDER BY generated_at ASC",
            (seed_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_excavation_cycle(self, cycle: ExcavationCycle) -> None:
        self._conn.execute(
            """INSERT INTO excavation_cycles
               (id, seed_id, candidates_requested, candidates_parsed_ok,
                candidates_failed, total_cost_usd, total_input_tokens,
                total_output_tokens, iteration, refined_from_candidate_id, started_at, completed_at, usage_accounted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cycle.id,
                cycle.seed_id,
                cycle.candidates_requested,
                cycle.candidates_parsed_ok,
                cycle.candidates_failed,
                cycle.total_cost_usd,
                cycle.total_input_tokens,
                cycle.total_output_tokens,
                cycle.iteration,
                cycle.refined_from_candidate_id,
                cycle.started_at,
                cycle.completed_at,
                int(cycle.usage_accounted),
            ),
        )

    def get_today_spend_usd(self) -> float:
        row = self._conn.execute(
            """SELECT COALESCE(SUM(cost), 0) AS total FROM (
                 SELECT total_cost_usd AS cost FROM excavation_cycles
                 WHERE date(started_at) = date('now') AND usage_accounted = 0
                 UNION ALL
                 SELECT COALESCE(s.actual_cost_usd, r.reserved_cost_usd) AS cost
                 FROM model_call_reservations r
                 LEFT JOIN model_call_settlements s ON s.call_id = r.id
                 WHERE date(r.started_at) = date('now')
               )"""
        ).fetchone()
        return float(row["total"])

    def get_usage_summary(self) -> UsageSummary:
        """Combine request usage with cycles that predate request accounting.

        Unsettled requests have no known token usage or cost. Report their
        reservations separately; get_today_spend_usd includes them in the
        budget only when they started today (UTC).
        """
        usage = self._conn.execute(
            """SELECT COALESCE(SUM(cost), 0) AS cost,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens
               FROM (
                 SELECT total_cost_usd AS cost, total_input_tokens AS input_tokens,
                        total_output_tokens AS output_tokens
                 FROM excavation_cycles WHERE usage_accounted = 0
                 UNION ALL
                 SELECT actual_cost_usd, input_tokens, output_tokens
                 FROM model_call_settlements
               )"""
        ).fetchone()
        requests = self._conn.execute(
            """SELECT COUNT(s.call_id) AS settled,
                      COUNT(*) - COUNT(s.call_id) AS pending,
                      COALESCE(SUM(CASE WHEN s.call_id IS NULL
                          THEN r.reserved_cost_usd ELSE 0 END), 0) AS reserved
               FROM model_call_reservations r
               LEFT JOIN model_call_settlements s ON s.call_id = r.id"""
        ).fetchone()
        return UsageSummary(
            estimated_cost_usd=float(usage["cost"]),
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
            settled_requests=int(requests["settled"]),
            pending_requests=int(requests["pending"]),
            reserved_cost_usd=float(requests["reserved"]),
        )

    def reserve_model_call(self, call_id: str, model: str, amount: float, limit: float) -> bool:
        """Atomically reserve a budget shared by every client/process using this ledger."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            if self.get_today_spend_usd() + amount > limit:
                self._conn.execute("ROLLBACK")
                return False
            self._conn.execute(
                "INSERT INTO model_call_reservations VALUES (?, ?, ?, ?)",
                (call_id, model, amount, now_iso()),
            )
            self._conn.execute("COMMIT")
            return True
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def settle_model_call(
        self, call_id: str, cost: float, input_tokens: int, output_tokens: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO model_call_settlements VALUES (?, ?, ?, ?, ?)",
            (call_id, cost, input_tokens, output_tokens, now_iso()),
        )

    def list_excavation_cycles(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM excavation_cycles ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Tool invocations (miner-registered skills, hanno/skills/)
    # ------------------------------------------------------------------

    def insert_tool_invocation(self, inv: ToolInvocation) -> None:
        self._conn.execute(
            """INSERT INTO tool_invocations
               (id, candidate_id, skill_name, input_text, stdout, stderr,
                exit_code, duration_ms, timed_out, invoked_at, phase)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inv.id,
                inv.candidate_id,
                inv.skill_name,
                inv.input_text,
                inv.stdout,
                inv.stderr,
                inv.exit_code,
                inv.duration_ms,
                int(inv.timed_out),
                inv.invoked_at,
                inv.phase,
            ),
        )

    def list_tool_invocations_by_candidate(self, candidate_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tool_invocations WHERE candidate_id = ? ORDER BY invoked_at ASC",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Arm selection (adaptive model/temperature/variant/reasoning_effort)
    # ------------------------------------------------------------------

    def insert_arm_score_event(self, event: ArmScoreEvent) -> None:
        self._conn.execute(
            """INSERT INTO arm_score_events
               (id, domain, model, temperature, prompt_variant, reasoning_effort,
                candidate_id, score, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.domain,
                event.model,
                event.temperature,
                event.prompt_variant,
                event.reasoning_effort,
                event.candidate_id,
                event.score,
                event.recorded_at,
            ),
        )

    def get_arm_stats(self, domain: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT model, temperature, prompt_variant, reasoning_effort,
                      COUNT(*) AS pulls, AVG(score) AS avg_score
               FROM arm_score_events WHERE domain = ?
               GROUP BY model, temperature, prompt_variant, reasoning_effort""",
            (domain,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Fingerprints, Collisions, Reducer Versions (Module 3)
    # ------------------------------------------------------------------

    def insert_fingerprint(
        self,
        hash: str,
        entity_type: str,
        entity_id: str,
        compressed_vector: list[float],
        full_vector: list[float],
        embedding_model: str,
        compression_model_version: str,
    ) -> None:
        import uuid

        self._conn.execute(
            """INSERT INTO fingerprints
               (id, hash, entity_type, entity_id, compressed_vector, full_vector,
                embedding_model, compression_model_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                hash,
                entity_type,
                entity_id,
                json.dumps(compressed_vector),
                json.dumps(full_vector),
                embedding_model,
                compression_model_version,
                now_iso(),
            ),
        )

    def list_fingerprints_by_entity_type(self, entity_type: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM fingerprints WHERE entity_type = ?", (entity_type,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_fingerprints(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM fingerprints").fetchone()
        return int(row["c"])

    def list_all_full_vectors(self) -> list[list[float]]:
        rows = self._conn.execute("SELECT full_vector FROM fingerprints").fetchall()
        return [json.loads(r["full_vector"]) for r in rows]

    def get_full_vectors_for_entities(
        self,
        entity_type: str,
        entity_ids: list[str],
    ) -> dict[str, list[float]]:
        if not entity_ids:
            return {}
        placeholders = ",".join("?" * len(entity_ids))
        rows = self._conn.execute(
            f"SELECT entity_id, full_vector FROM fingerprints "
            f"WHERE entity_type = ? AND entity_id IN ({placeholders})",
            (entity_type, *entity_ids),
        ).fetchall()
        return {r["entity_id"]: json.loads(r["full_vector"]) for r in rows}

    def insert_collision_event(
        self,
        query_fingerprint_hash: str,
        matched_fingerprint_hash: str | None,
        similarity_score: float | None,
        classification: str,
    ) -> None:
        import uuid

        self._conn.execute(
            """INSERT INTO collision_events
               (id, query_fingerprint_hash, matched_fingerprint_hash,
                similarity_score, classification, checked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                query_fingerprint_hash,
                matched_fingerprint_hash,
                similarity_score,
                classification,
                now_iso(),
            ),
        )

    def get_active_reducer_version(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM reducer_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def insert_reducer_version(
        self,
        version: int,
        input_dimensions: int,
        output_dimensions: int,
        mean_json: str,
        components_json: str,
        fitted_on_count: int,
    ) -> None:
        import uuid

        self._conn.execute(
            """INSERT INTO reducer_versions
               (id, version, input_dimensions, output_dimensions, mean_json,
                components_json, fitted_on_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                version,
                input_dimensions,
                output_dimensions,
                mean_json,
                components_json,
                fitted_on_count,
                now_iso(),
            ),
        )

    # ------------------------------------------------------------------
    # Calibration & Config Snapshots
    # ------------------------------------------------------------------

    def insert_calibration_event(
        self,
        module: str,
        parameter: str,
        reason: str,
        old_value: str | None = None,
        new_value: str | None = None,
        supporting_data: str | None = None,
    ) -> None:
        import uuid

        self._conn.execute(
            """INSERT INTO calibration_events
               (id, module, parameter, old_value, new_value, reason, supporting_data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                module,
                parameter,
                old_value,
                new_value,
                reason,
                supporting_data,
                now_iso(),
            ),
        )

    def insert_config_snapshot(self, snapshot_json: str) -> None:
        import uuid

        self._conn.execute(
            "INSERT INTO config_snapshots (id, snapshot_json, created_at) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), snapshot_json, now_iso()),
        )

    def get_latest_config_snapshot(self) -> str | None:
        row = self._conn.execute(
            "SELECT snapshot_json FROM config_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["snapshot_json"] if row else None

    def list_calibration_events(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM calibration_events ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Reseed Events
    # ------------------------------------------------------------------

    def insert_reseed_event(self, event: ReseedEvent) -> None:
        self._conn.execute(
            """INSERT INTO reseed_events
               (id, original_seed_id, parent_seed_id, generated_reseed_id, trigger_type,
                trigger_conditions, triggering_candidates, chain_depth, operator_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.original_seed_id,
                event.parent_seed_id,
                event.generated_reseed_id,
                event.trigger_type,
                json.dumps(event.trigger_conditions) if event.trigger_conditions else None,
                json.dumps(event.triggering_candidates) if event.triggering_candidates else None,
                event.chain_depth,
                event.operator_id,
                event.created_at,
            ),
        )

    def get_reseed_event_by_generated_seed(self, generated_reseed_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM reseed_events WHERE generated_reseed_id = ?",
            (generated_reseed_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_reseed_events_by_seed(self, seed_id: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM reseed_events
               WHERE original_seed_id = ? OR parent_seed_id = ? OR generated_reseed_id = ?
               ORDER BY created_at ASC""",
            (seed_id, seed_id, seed_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Lean Verifications
    # ------------------------------------------------------------------

    def insert_lean_verification(self, verification: LeanVerification) -> None:
        self._conn.execute(
            """INSERT INTO lean_verifications
               (id, candidate_id, lean_code, translation_confidence, translation_attempt_number,
                lean_stdout, lean_stderr, result, execution_time_ms, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                verification.id,
                verification.candidate_id,
                verification.lean_code,
                verification.translation_confidence,
                verification.translation_attempt_number,
                verification.lean_stdout,
                verification.lean_stderr,
                verification.result,
                verification.execution_time_ms,
                verification.verified_at,
            ),
        )

    def list_lean_verifications_by_candidate(self, candidate_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM lean_verifications WHERE candidate_id = ? ORDER BY verified_at ASC",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_mathematics_candidates_without_lean_check(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM candidates
               WHERE domain = 'mathematics' AND parse_status = 'ok' AND formal_statement IS NOT NULL
                 AND id NOT IN (SELECT candidate_id FROM lean_verifications)
               ORDER BY generated_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Discoveries
    # ------------------------------------------------------------------

    def insert_discovery(self, discovery: Discovery) -> None:
        self._conn.execute(
            """INSERT INTO discoveries
               (id, candidate_id, fingerprint_hash, seed_id, domain, tier, statement,
                formal_statement, sieve_score, verification_result, lean_verified,
                human_reviewed, human_reviewer_id, human_verdict, human_notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                discovery.id,
                discovery.candidate_id,
                discovery.fingerprint_hash,
                discovery.seed_id,
                discovery.domain,
                discovery.tier,
                discovery.statement,
                discovery.formal_statement,
                discovery.sieve_score,
                discovery.verification_result,
                None if discovery.lean_verified is None else int(discovery.lean_verified),
                int(discovery.human_reviewed),
                discovery.human_reviewer_id,
                discovery.human_verdict,
                discovery.human_notes,
                discovery.created_at,
            ),
        )

    def list_discoveries(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM discoveries ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    TABLES = [
        "seeds",
        "seed_status_events",
        "seed_stat_events",
        "candidates",
        "excavation_cycles",
        "fingerprints",
        "collision_events",
        "reducer_versions",
        "sieve_evaluations",
        "lean_verifications",
        "science_verifications",
        "philosophy_verifications",
        "discoveries",
        "human_reviews",
        "calibration_events",
        "config_snapshots",
        "reseed_events",
        "arm_score_events",
        "tool_invocations",
        "model_call_reservations",
        "model_call_settlements",
    ]

    def export_json(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump = {}
        for table in self.TABLES:
            rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
            dump[table] = [dict(r) for r in rows]
        with open(path, "w") as f:
            json.dump(dump, f, indent=2, default=str)
