-- Project Hanno — Ledger schema.
-- Append-only by design: every table below has AFTER UPDATE / AFTER DELETE
-- triggers that abort the statement. Status/counter fields are never mutated
-- in place — they are modeled as separate event tables and the "current"
-- value is derived by querying the latest event.

PRAGMA foreign_keys = ON;

-- ============================================================
-- SEEDS
-- ============================================================

CREATE TABLE IF NOT EXISTS seeds (
    id                  TEXT PRIMARY KEY,
    text                TEXT NOT NULL,
    type                TEXT NOT NULL CHECK(type IN ('human','ai-generated','derivative')),
    domain              TEXT NOT NULL CHECK(domain IN ('mathematics','science','philosophy','cross-domain')),
    submitter_id        TEXT,
    parent_discovery_id TEXT,
    submitted_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seed_status_events (
    id          TEXT PRIMARY KEY,
    seed_id     TEXT NOT NULL REFERENCES seeds(id),
    status      TEXT NOT NULL CHECK(status IN ('queued','active','exhausted','retired')),
    reason      TEXT,
    changed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seed_stat_events (
    id                  TEXT PRIMARY KEY,
    seed_id             TEXT NOT NULL REFERENCES seeds(id),
    cycles_run_delta    INTEGER NOT NULL DEFAULT 0,
    candidates_delta    INTEGER NOT NULL DEFAULT 0,
    discoveries_delta   INTEGER NOT NULL DEFAULT 0,
    recorded_at         TEXT NOT NULL
);

-- ============================================================
-- CANDIDATES & EXCAVATION
-- ============================================================

CREATE TABLE IF NOT EXISTS candidates (
    id                      TEXT PRIMARY KEY,
    seed_id                 TEXT NOT NULL REFERENCES seeds(id),
    domain                  TEXT NOT NULL,
    temperature             REAL NOT NULL,
    prompt_variant          TEXT NOT NULL,
    model                   TEXT NOT NULL,
    prompt_text             TEXT NOT NULL,   -- the full rendered prompt actually sent to the model
    raw_text                TEXT NOT NULL,
    core_claim              TEXT,
    formal_statement        TEXT,
    intuition               TEXT,
    connections             TEXT,   -- JSON array of strings
    self_assessed_novelty   TEXT,
    identified_weaknesses   TEXT,
    parse_status            TEXT NOT NULL CHECK(parse_status IN ('ok','failed')),
    parse_error             TEXT,
    duplicate_status        TEXT CHECK(duplicate_status IN ('NOVEL','NEAR_DUPLICATE','EXACT_DUPLICATE')),
    exploration_signal      TEXT CHECK(exploration_signal IN ('continue','branch_point','dead_end')),
    suggested_seeds         TEXT,   -- JSON array of strings
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    reasoning_effort        TEXT CHECK(reasoning_effort IN ('standard','deep')),
    self_rated_novelty      REAL,
    self_rated_rigor        REAL,
    path_score              REAL,
    numeric_check           TEXT,   -- JSON {assignments, equation}, if the model supplied one
    numeric_check_result    INTEGER CHECK(numeric_check_result IN (0,1)),
    claim_type              TEXT CHECK(claim_type IN ('construction','asymptotic','structural')),
    steering_hint           TEXT,   -- strategic angle assigned to this candidate slot, if any
    iteration               INTEGER NOT NULL DEFAULT 1,   -- iterative-mining depth this candidate was produced at
    refined_from_candidate_id TEXT REFERENCES candidates(id),  -- the prior-iteration winner this candidate's thread refines, if any
    generated_at            TEXT NOT NULL
);

-- ============================================================
-- TOOL INVOCATIONS (miner-registered skills the model called
-- mid-generation to actually test an idea — see hanno/skills/)
-- ============================================================

CREATE TABLE IF NOT EXISTS tool_invocations (
    id              TEXT PRIMARY KEY,
    candidate_id    TEXT NOT NULL REFERENCES candidates(id),
    skill_name      TEXT NOT NULL,
    input_text      TEXT NOT NULL,
    stdout          TEXT,
    stderr          TEXT,
    exit_code       INTEGER,
    duration_ms     INTEGER,
    timed_out       INTEGER NOT NULL DEFAULT 0 CHECK(timed_out IN (0,1)),
    invoked_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_candidate ON tool_invocations(candidate_id);

-- ============================================================
-- ARM SELECTION (adaptive tuning of model/temperature/variant/
-- reasoning_effort — "which sailing heading is working")
-- ============================================================

CREATE TABLE IF NOT EXISTS arm_score_events (
    id                      TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL,
    model                   TEXT NOT NULL,
    temperature             REAL NOT NULL,
    prompt_variant          TEXT NOT NULL,
    reasoning_effort        TEXT NOT NULL CHECK(reasoning_effort IN ('standard','deep')),
    candidate_id            TEXT NOT NULL REFERENCES candidates(id),
    score                   REAL NOT NULL,
    recorded_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arm_score_events_domain ON arm_score_events(domain);

CREATE TABLE IF NOT EXISTS excavation_cycles (
    id                      TEXT PRIMARY KEY,
    seed_id                 TEXT NOT NULL REFERENCES seeds(id),
    candidates_requested    INTEGER NOT NULL,
    candidates_parsed_ok    INTEGER NOT NULL,
    candidates_failed       INTEGER NOT NULL,
    total_cost_usd          REAL NOT NULL,
    total_input_tokens      INTEGER NOT NULL DEFAULT 0,
    total_output_tokens     INTEGER NOT NULL DEFAULT 0,
    iteration               INTEGER NOT NULL DEFAULT 1,
    refined_from_candidate_id TEXT REFERENCES candidates(id),
    started_at              TEXT NOT NULL,
    completed_at            TEXT NOT NULL
);

-- ============================================================
-- FINGERPRINTING (Step 4 / Module 3)
--
-- Generalized beyond the spec's literal FINGERPRINT model (which only lists
-- candidate_id) to cover seeds as well: General-spec.txt:343-347 requires
-- seed non-duplication to be evaluated, and the only correct technical
-- mechanism for "same idea, different words" is semantic fingerprinting
-- (General-spec.txt:496-500) — the same mechanism used for candidates.
-- entity_type/entity_id replaces the spec's candidate_id-only field.
-- Similarity comparisons are scoped within the same entity_type: a seed is
-- only compared against other seeds, a candidate only against other
-- candidates. Cross-domain comparison (General-spec.txt:578-583) still
-- applies within the candidate pool across mathematics/science/philosophy.
-- ============================================================

CREATE TABLE IF NOT EXISTS fingerprints (
    id                          TEXT PRIMARY KEY,
    -- hash is deliberately NOT unique/PK: the entire point of exact-duplicate
    -- detection is that two different entities can produce the same hash.
    hash                        TEXT NOT NULL,
    entity_type                 TEXT NOT NULL CHECK(entity_type IN ('seed','candidate')),
    entity_id                   TEXT NOT NULL,
    compressed_vector           TEXT NOT NULL,  -- JSON array of floats
    full_vector                 TEXT NOT NULL,  -- JSON array of floats
    embedding_model              TEXT NOT NULL,
    compression_model_version   TEXT NOT NULL,
    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fingerprints_hash ON fingerprints(hash);
CREATE INDEX IF NOT EXISTS idx_fingerprints_entity_type ON fingerprints(entity_type);

CREATE TABLE IF NOT EXISTS collision_events (
    id                          TEXT PRIMARY KEY,
    query_fingerprint_hash      TEXT NOT NULL,
    matched_fingerprint_hash    TEXT,
    similarity_score            REAL,
    classification              TEXT NOT NULL CHECK(classification IN ('NOVEL','NEAR_DUPLICATE','EXACT_DUPLICATE')),
    checked_at                  TEXT NOT NULL
);

-- Dimensionality-reduction model versions (General-spec.txt:524-534).
-- Raw truncation is used below calibration_minimum_fingerprints; once that
-- threshold is crossed, a PCA model is fit and its parameters stored here.
-- Refit every recalibration_interval new fingerprints thereafter.
CREATE TABLE IF NOT EXISTS reducer_versions (
    id                  TEXT PRIMARY KEY,
    version              INTEGER NOT NULL,
    input_dimensions     INTEGER NOT NULL,
    output_dimensions    INTEGER NOT NULL,
    mean_json            TEXT NOT NULL,   -- JSON array, PCA mean_ vector
    components_json       TEXT NOT NULL,  -- JSON 2D array, PCA components_ matrix
    fitted_on_count        INTEGER NOT NULL,
    created_at              TEXT NOT NULL
);

-- ============================================================
-- SIEVE (Step 5 — schema present now, unused until then)
-- ============================================================

CREATE TABLE IF NOT EXISTS sieve_evaluations (
    id                          TEXT PRIMARY KEY,
    candidate_id                TEXT NOT NULL REFERENCES candidates(id),
    coherence_score              REAL,
    coherence_reasoning          TEXT,
    novelty_score                REAL,
    novelty_reasoning            TEXT,
    novelty_sources_checked      TEXT,  -- JSON array
    significance_score           REAL,
    significance_reasoning       TEXT,
    fertility_score               REAL,
    fertility_reasoning           TEXT,
    formalizability_score         REAL,
    formalizability_reasoning     TEXT,
    final_score                   REAL,
    passed                        INTEGER NOT NULL CHECK(passed IN (0,1)),
    fail_reason                   TEXT,
    evaluated_at                   TEXT NOT NULL
);

-- ============================================================
-- DOMAIN VERIFICATION (Steps 6, 9, 10)
-- ============================================================

CREATE TABLE IF NOT EXISTS lean_verifications (
    id                          TEXT PRIMARY KEY,
    candidate_id                TEXT NOT NULL REFERENCES candidates(id),
    lean_code                    TEXT,
    translation_confidence        REAL,
    translation_attempt_number    INTEGER,
    lean_stdout                   TEXT,
    lean_stderr                   TEXT,
    result                         TEXT CHECK(result IN ('VERIFIED','KNOWN','UNTRANSLATABLE','MALFORMED','TIMEOUT')),
    execution_time_ms              INTEGER,
    verified_at                    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS science_verifications (
    id                           TEXT PRIMARY KEY,
    candidate_id                 TEXT NOT NULL REFERENCES candidates(id),
    extracted_predictions         TEXT,  -- JSON array
    falsification_tests           TEXT,  -- JSON array
    literature_sources_checked    TEXT,  -- JSON array
    contradicting_results         TEXT,  -- JSON array
    result                          TEXT CHECK(result IN ('VERIFIED','CONTRADICTED','REDUNDANT','UNFALSIFIABLE','DISCONNECTED')),
    reasoning                       TEXT,
    verified_at                     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS philosophy_verifications (
    id                           TEXT PRIMARY KEY,
    candidate_id                 TEXT NOT NULL REFERENCES candidates(id),
    reconstructed_premises        TEXT,  -- JSON array
    reconstructed_conclusion      TEXT,
    validity_assessment           TEXT,
    consistency_assessment        TEXT,
    term_consistency_assessment   TEXT,
    closest_existing_positions    TEXT,  -- JSON array
    distinguishing_features       TEXT,
    result                          TEXT CHECK(result IN ('VERIFIED','INVALID','INCONSISTENT','EQUIVOCATING','RESTATEMENT','UNSTRUCTURED')),
    reasoning                       TEXT,
    verified_at                     TEXT NOT NULL
);

-- ============================================================
-- DISCOVERIES & HUMAN REVIEW (Steps 6+, 7)
-- ============================================================

CREATE TABLE IF NOT EXISTS discoveries (
    id                     TEXT PRIMARY KEY,
    candidate_id           TEXT NOT NULL REFERENCES candidates(id),
    fingerprint_hash       TEXT,
    seed_id                TEXT NOT NULL REFERENCES seeds(id),
    domain                 TEXT NOT NULL,
    tier                   TEXT,
    statement              TEXT,
    formal_statement       TEXT,
    sieve_score            REAL,
    verification_result    TEXT,
    lean_verified           INTEGER CHECK(lean_verified IN (0,1)),
    human_reviewed           INTEGER NOT NULL DEFAULT 0 CHECK(human_reviewed IN (0,1)),
    human_reviewer_id        TEXT,
    human_verdict             TEXT CHECK(human_verdict IN ('NOVEL','KNOWN','UNCERTAIN','REJECTED')),
    human_notes               TEXT,
    created_at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_reviews (
    id                          TEXT PRIMARY KEY,
    target_id                   TEXT NOT NULL,
    target_type                 TEXT NOT NULL CHECK(target_type IN ('discovery','candidate')),
    reviewer_id                 TEXT NOT NULL,
    domain                      TEXT NOT NULL,
    verdict                     TEXT NOT NULL CHECK(verdict IN ('NOVEL','KNOWN','UNCERTAIN','REJECTED')),
    novelty_notes                TEXT,
    significance_rating          INTEGER CHECK(significance_rating BETWEEN 1 AND 5),
    suggested_related_work       TEXT,  -- JSON array
    suggested_follow_up_seeds    TEXT,  -- JSON array
    reviewed_at                   TEXT NOT NULL
);

-- ============================================================
-- CALIBRATION, CONFIG SNAPSHOTS, RESEED EVENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS calibration_events (
    id              TEXT PRIMARY KEY,
    module          TEXT NOT NULL,
    parameter       TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT NOT NULL,
    supporting_data TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id              TEXT PRIMARY KEY,
    snapshot_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reseed_events (
    id                      TEXT PRIMARY KEY,
    original_seed_id        TEXT NOT NULL REFERENCES seeds(id),
    parent_seed_id           TEXT NOT NULL REFERENCES seeds(id),
    generated_reseed_id      TEXT NOT NULL REFERENCES seeds(id),
    trigger_type             TEXT NOT NULL CHECK(trigger_type IN ('manual','automatic','cartographer')),
    trigger_conditions        TEXT,  -- JSON array
    triggering_candidates     TEXT,  -- JSON array of candidate IDs
    chain_depth                INTEGER NOT NULL,
    operator_id                 TEXT,
    created_at                   TEXT NOT NULL
);

-- ============================================================
-- APPEND-ONLY ENFORCEMENT
-- ============================================================

CREATE TRIGGER IF NOT EXISTS seeds_no_update AFTER UPDATE ON seeds
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on seeds'); END;
CREATE TRIGGER IF NOT EXISTS seeds_no_delete AFTER DELETE ON seeds
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on seeds'); END;

CREATE TRIGGER IF NOT EXISTS seed_status_events_no_update AFTER UPDATE ON seed_status_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on seed_status_events'); END;
CREATE TRIGGER IF NOT EXISTS seed_status_events_no_delete AFTER DELETE ON seed_status_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on seed_status_events'); END;

CREATE TRIGGER IF NOT EXISTS seed_stat_events_no_update AFTER UPDATE ON seed_stat_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on seed_stat_events'); END;
CREATE TRIGGER IF NOT EXISTS seed_stat_events_no_delete AFTER DELETE ON seed_stat_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on seed_stat_events'); END;

CREATE TRIGGER IF NOT EXISTS candidates_no_update AFTER UPDATE ON candidates
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on candidates'); END;
CREATE TRIGGER IF NOT EXISTS candidates_no_delete AFTER DELETE ON candidates
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on candidates'); END;

CREATE TRIGGER IF NOT EXISTS excavation_cycles_no_update AFTER UPDATE ON excavation_cycles
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on excavation_cycles'); END;
CREATE TRIGGER IF NOT EXISTS excavation_cycles_no_delete AFTER DELETE ON excavation_cycles
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on excavation_cycles'); END;

CREATE TRIGGER IF NOT EXISTS fingerprints_no_update AFTER UPDATE ON fingerprints
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on fingerprints'); END;
CREATE TRIGGER IF NOT EXISTS fingerprints_no_delete AFTER DELETE ON fingerprints
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on fingerprints'); END;

CREATE TRIGGER IF NOT EXISTS collision_events_no_update AFTER UPDATE ON collision_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on collision_events'); END;
CREATE TRIGGER IF NOT EXISTS collision_events_no_delete AFTER DELETE ON collision_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on collision_events'); END;

CREATE TRIGGER IF NOT EXISTS reducer_versions_no_update AFTER UPDATE ON reducer_versions
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on reducer_versions'); END;
CREATE TRIGGER IF NOT EXISTS reducer_versions_no_delete AFTER DELETE ON reducer_versions
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on reducer_versions'); END;

CREATE TRIGGER IF NOT EXISTS sieve_evaluations_no_update AFTER UPDATE ON sieve_evaluations
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on sieve_evaluations'); END;
CREATE TRIGGER IF NOT EXISTS sieve_evaluations_no_delete AFTER DELETE ON sieve_evaluations
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on sieve_evaluations'); END;

CREATE TRIGGER IF NOT EXISTS lean_verifications_no_update AFTER UPDATE ON lean_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on lean_verifications'); END;
CREATE TRIGGER IF NOT EXISTS lean_verifications_no_delete AFTER DELETE ON lean_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on lean_verifications'); END;

CREATE TRIGGER IF NOT EXISTS science_verifications_no_update AFTER UPDATE ON science_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on science_verifications'); END;
CREATE TRIGGER IF NOT EXISTS science_verifications_no_delete AFTER DELETE ON science_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on science_verifications'); END;

CREATE TRIGGER IF NOT EXISTS philosophy_verifications_no_update AFTER UPDATE ON philosophy_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on philosophy_verifications'); END;
CREATE TRIGGER IF NOT EXISTS philosophy_verifications_no_delete AFTER DELETE ON philosophy_verifications
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on philosophy_verifications'); END;

CREATE TRIGGER IF NOT EXISTS discoveries_no_update AFTER UPDATE ON discoveries
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on discoveries'); END;
CREATE TRIGGER IF NOT EXISTS discoveries_no_delete AFTER DELETE ON discoveries
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on discoveries'); END;

CREATE TRIGGER IF NOT EXISTS human_reviews_no_update AFTER UPDATE ON human_reviews
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on human_reviews'); END;
CREATE TRIGGER IF NOT EXISTS human_reviews_no_delete AFTER DELETE ON human_reviews
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on human_reviews'); END;

CREATE TRIGGER IF NOT EXISTS calibration_events_no_update AFTER UPDATE ON calibration_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on calibration_events'); END;
CREATE TRIGGER IF NOT EXISTS calibration_events_no_delete AFTER DELETE ON calibration_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on calibration_events'); END;

CREATE TRIGGER IF NOT EXISTS config_snapshots_no_update AFTER UPDATE ON config_snapshots
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on config_snapshots'); END;
CREATE TRIGGER IF NOT EXISTS config_snapshots_no_delete AFTER DELETE ON config_snapshots
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on config_snapshots'); END;

CREATE TRIGGER IF NOT EXISTS reseed_events_no_update AFTER UPDATE ON reseed_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on reseed_events'); END;
CREATE TRIGGER IF NOT EXISTS reseed_events_no_delete AFTER DELETE ON reseed_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on reseed_events'); END;

CREATE TRIGGER IF NOT EXISTS arm_score_events_no_update AFTER UPDATE ON arm_score_events
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on arm_score_events'); END;
CREATE TRIGGER IF NOT EXISTS arm_score_events_no_delete AFTER DELETE ON arm_score_events
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on arm_score_events'); END;

CREATE TRIGGER IF NOT EXISTS tool_invocations_no_update AFTER UPDATE ON tool_invocations
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on tool_invocations'); END;
CREATE TRIGGER IF NOT EXISTS tool_invocations_no_delete AFTER DELETE ON tool_invocations
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on tool_invocations'); END;
