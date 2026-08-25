# Project Hanno — MVP Architecture (Steps 1–3)

Blueprint for the build strategy's Steps 1–3: **Foundation (Config + Ledger)**, **Seed Management**, and **Excavator (Mathematics only)**. No code in this document — this is the design to implement against. Later steps (Fingerprint Engine, Sieve, Lean Verifier, Cartographer, ...) are noted only where they constrain today's design (e.g. schema must exist now per Step 1 deliverables), not designed in depth here.

> **Spec gap noted:** `General-spec.txt` truncates mid-Section 16 (Risk Register) and never delivers Sections 17–21 (Development Phases, Seed Corpora for math/science/philosophy, Out-of-Scope list). The strategy doc references "the 10 mathematics seeds from tech spec Section 18" — **this corpus does not exist in the current spec file.** Step 2 cannot be fully validated (success criteria: "All 10 math corpus seeds load and validate") until this is authored. Flagging as a blocking dependency for Step 2, not solved here.

---

## 1. Tech Stack Decisions

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Best ecosystem overlap with LLM SDKs, embeddings (sentence-transformers, Step 4), and Lean tooling wrappers (Step 6). One language for the whole pipeline. |
| CLI | `typer` | Type-hint driven, minimal boilerplate for the 6 stub commands (`mine`, `seeds`, `discoveries`, `stats`, `report`, `export`). |
| Database | `sqlite3` (stdlib), raw SQL | Strategy doc Step 1 "Honest Risks" explicitly warns against over-engineering the schema. No ORM — plain tables, plain queries, one file (`hanno.db`). |
| Config | YAML file + `pydantic` models | Typed validation, nested sections mirroring spec Section 14, environment-variable override via `pydantic-settings`. |
| LLM client | `anthropic` SDK, async | Excavator needs parallel calls (`max_parallel_calls`, default 10) — `AsyncAnthropic` + `asyncio.Semaphore`. |
| Concurrency | `asyncio` | Native fit for the SDK; simpler than threads for I/O-bound API calls. |
| Logging | stdlib `logging`, dual handler (console + rotating file), JSON formatter for the file handler | Matches Step 1 "Basic structured logging" deliverable. |
| Testing | `pytest` | Standard; strategy doc calls out dedicated test suites for append-only enforcement and (later) fingerprinting. |

Deliberately **not** used yet: any vector DB, any ORM, any task queue, any web framework. All are premature for Steps 1–3.

---

## 2. Project Structure

```
HANNO/
├── General-spec.txt
├── mvp-strategy-spec.md
├── ARCHITECTURE.md                # this document
├── pyproject.toml
├── config/
│   └── default.yaml               # all Section-14 parameters, documented inline
├── data/
│   └── hanno.db                   # gitignored, created on first run
├── logs/
│   └── hanno.log                  # gitignored
├── hanno/
│   ├── __init__.py
│   ├── config.py                  # HannoConfig pydantic models + loader
│   ├── logging_setup.py
│   ├── ledger/
│   │   ├── __init__.py
│   │   ├── schema.sql             # full DDL, all record types (Step 1 requires ALL tables now)
│   │   ├── db.py                  # connection mgmt, schema init/migration check
│   │   ├── models.py              # dataclasses: Seed, Candidate, CalibrationEvent, ...
│   │   └── repository.py          # Ledger class — insert/read only, no update/delete
│   ├── seeds/
│   │   ├── __init__.py
│   │   ├── validation.py          # rule-based checks (Step 2 simplification, documented)
│   │   ├── queue.py                # priority ordering logic
│   │   └── manager.py             # SeedManager: submit / list / transition
│   ├── excavator/
│   │   ├── __init__.py
│   │   ├── prompts.py              # 5 universal + 3 math-specific variants
│   │   ├── client.py                # Claude API wrapper, retry/backoff
│   │   ├── spend_tracker.py         # hard daily-cap enforcement
│   │   ├── parser.py                # structured-output parsing + validation
│   │   └── pipeline.py              # orchestrates one excavation cycle
│   └── cli/
│       ├── __init__.py
│       ├── main.py                  # typer app, registers subcommands
│       └── commands/
│           ├── mine.py
│           ├── seeds.py
│           ├── discoveries.py       # stub only — no discoveries minted until Step 6+
│           ├── stats.py
│           ├── report.py            # stub only — full report needs Step 7
│           └── export.py
└── tests/
    ├── test_ledger_append_only.py
    ├── test_config.py
    ├── test_seed_validation.py
    └── test_excavator_parser.py
```

---

## 3. Ledger Schema (Step 1 — all tables created now)

One SQLite file. Every table is insert-only at the application layer; enforced twice (belt + suspenders):

1. **DB-level**: `AFTER UPDATE` / `AFTER DELETE` triggers on every table that `RAISE(ABORT, 'append-only violation')`.
2. **App-level**: `Ledger` class exposes only `insert_*` and `get_*`/`list_*` methods — no `update_*`/`delete_*` method exists in the codebase, so it is not reachable through the repository API even by mistake.

Status fields (seed status, discovery review status, etc.) are **never mutated in place**. Current status is derived by querying the latest event row for that entity.

```sql
-- hanno/ledger/schema.sql (abridged — full column set per General-spec.txt Section 12)

CREATE TABLE seeds (
    id                  TEXT PRIMARY KEY,
    text                TEXT NOT NULL,
    type                TEXT NOT NULL CHECK(type IN ('human','ai-generated','derivative')),
    domain              TEXT NOT NULL CHECK(domain IN ('mathematics','science','philosophy','cross-domain')),
    submitter_id        TEXT,
    parent_discovery_id TEXT,
    submitted_at        TEXT NOT NULL
);

CREATE TABLE seed_status_events (       -- append-only lifecycle, no status column update on seeds
    id          TEXT PRIMARY KEY,
    seed_id     TEXT NOT NULL REFERENCES seeds(id),
    status      TEXT NOT NULL CHECK(status IN ('queued','active','exhausted','retired')),
    reason      TEXT,
    changed_at  TEXT NOT NULL
);

CREATE TABLE seed_stat_events (          -- fertility inputs, one row per cycle/attribution, never summed in place
    id                  TEXT PRIMARY KEY,
    seed_id             TEXT NOT NULL REFERENCES seeds(id),
    cycles_run_delta    INTEGER NOT NULL DEFAULT 0,
    candidates_delta    INTEGER NOT NULL DEFAULT 0,
    discoveries_delta   INTEGER NOT NULL DEFAULT 0,
    recorded_at         TEXT NOT NULL
);

CREATE TABLE candidates (
    id                      TEXT PRIMARY KEY,
    seed_id                 TEXT NOT NULL REFERENCES seeds(id),
    domain                  TEXT NOT NULL,
    temperature             REAL NOT NULL,
    prompt_variant          TEXT NOT NULL,
    model                   TEXT NOT NULL,
    raw_text                TEXT NOT NULL,
    core_claim              TEXT,
    formal_statement        TEXT,
    intuition               TEXT,
    connections             TEXT,   -- JSON array
    self_assessed_novelty   TEXT,
    identified_weaknesses   TEXT,
    parse_status            TEXT NOT NULL CHECK(parse_status IN ('ok','failed')),
    generated_at            TEXT NOT NULL
);

CREATE TABLE excavation_cycles (         -- cost + timing accounting per cycle (Step 3 deliverable)
    id              TEXT PRIMARY KEY,
    seed_id         TEXT NOT NULL REFERENCES seeds(id),
    candidates_requested INTEGER NOT NULL,
    candidates_parsed_ok INTEGER NOT NULL,
    candidates_failed    INTEGER NOT NULL,
    total_cost_usd       REAL NOT NULL,
    started_at           TEXT NOT NULL,
    completed_at         TEXT NOT NULL
);

-- Tables required to exist per Step 1 deliverables but unused until later steps:
CREATE TABLE fingerprints ( ... );          -- Step 4
CREATE TABLE collision_events ( ... );      -- Step 4
CREATE TABLE sieve_evaluations ( ... );     -- Step 5
CREATE TABLE lean_verifications ( ... );    -- Step 6
CREATE TABLE science_verifications ( ... ); -- Step 9
CREATE TABLE philosophy_verifications ( ... ); -- Step 10
CREATE TABLE discoveries ( ... );           -- Step 6+
CREATE TABLE human_reviews ( ... );         -- Step 7
CREATE TABLE reseed_events ( ... );         -- Step 11 / Addendum 1

CREATE TABLE calibration_events (
    id              TEXT PRIMARY KEY,
    module          TEXT NOT NULL,
    parameter       TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT NOT NULL,
    supporting_data TEXT,
    created_at      TEXT NOT NULL
);

-- Append-only trigger, replicated per table:
CREATE TRIGGER seeds_no_update AFTER UPDATE ON seeds
BEGIN SELECT RAISE(ABORT, 'append-only: update forbidden on seeds'); END;
CREATE TRIGGER seeds_no_delete AFTER DELETE ON seeds
BEGIN SELECT RAISE(ABORT, 'append-only: delete forbidden on seeds'); END;
-- (repeated for every table above)
```

Full-detail column lists for `fingerprints`, `sieve_evaluations`, `lean_verifications`, `science_verifications`, `philosophy_verifications`, `discoveries`, `human_reviews`, `reseed_events` are taken verbatim from `General-spec.txt:1142-1246` and `General-spec.txt:1646-1657` when the schema file is actually written — they are structurally defined already, just not needed operationally until their respective steps.

---

## 4. Config Design (Step 1)

`config/default.yaml` mirrors `General-spec.txt` Section 14 exactly, one section per module:

```yaml
excavator:
  candidates_per_cycle_per_seed: 50
  temperatures: [0.3, 0.6, 0.8, 1.0]     # must stay within the Anthropic API's 0.0-1.0 range
  max_tokens_per_candidate: 2000
  domain_specific_variants_per_domain: 3
  parallel_execution: true
  max_parallel_calls: 10
  daily_spend_limit_usd: 25.00      # hard cap, enforced in code (Step 3 requirement)

fingerprint: { ... }   # present, unused until Step 4
sieve: { ... }         # present, unused until Step 5
lean_verifier: { ... } # present, unused until Step 6
ledger:
  database_path: "data/hanno.db"
  export_path: "data/exports/"
pipeline:
  domains_enabled: [mathematics]     # science/philosophy added at Steps 9-10
```

- `HannoConfig` (pydantic) loads the YAML, then overrides any field from environment variables prefixed `HANNO_` (e.g. `HANNO_EXCAVATOR__DAILY_SPEND_LIMIT_USD`).
- On every load, the loader diffs the resolved config against the last-known config snapshot stored in the ledger and writes one `calibration_events` row per changed parameter (module + parameter + old/new + reason=`"config file change"`). This satisfies Step 1's success criterion "Config changes are logged as calibration events automatically" without any manual bookkeeping.

---

## 5. Module Interfaces

### 5.1 `Ledger` (`hanno/ledger/repository.py`)

Read/insert-only surface. No `update_*` or `delete_*` methods exist.

```
Ledger.connect(db_path) -> Ledger
Ledger.init_schema()                                   # idempotent, runs schema.sql

Ledger.insert_seed(seed: Seed) -> None
Ledger.insert_seed_status_event(seed_id, status, reason) -> None
Ledger.get_seed(id) -> Seed | None
Ledger.list_seeds(domain=None, status=None) -> list[Seed]   # status resolved via latest status_event
Ledger.get_seed_current_status(seed_id) -> str

Ledger.insert_candidate(candidate: Candidate) -> None
Ledger.list_candidates_by_seed(seed_id) -> list[Candidate]

Ledger.insert_excavation_cycle(cycle: ExcavationCycle) -> None

Ledger.insert_calibration_event(event: CalibrationEvent) -> None

Ledger.export_json(path) -> None                        # full dump, all tables
```

### 5.2 `SeedManager` (`hanno/seeds/manager.py`)

```
SeedManager.submit(text, domain, type="human", submitter_id=None) -> SubmitResult
    # runs validate() first; on failure returns feedback, never writes to ledger
SeedManager.validate(text, domain) -> ValidationResult
    # Step 2 simplification: rule-based only —
    #   length bounds (10-200 words), domain keyword presence,
    #   naive text-similarity duplicate flag (real fingerprint similarity is Step 4)
SeedManager.next_for_domain(domain) -> Seed | None
    # priority queue: fertility (default 0 for new) > recency boost > domain balance
SeedManager.mark_exhausted(seed_id, reason) -> None
    # called after 3 consecutive zero-sieve-pass cycles (logic activates fully at Step 5)
```

### 5.3 Excavator (`hanno/excavator/pipeline.py`)

```
ExcavatorPipeline.run_cycle(seed: Seed, n_candidates: int, config: HannoConfig) -> CycleResult
    for each of n_candidates:
        pick (temperature, prompt_variant) combination (round-robin across the 4x8 grid)
        ClaudeClient.generate(prompt, temperature, max_tokens)   # async, semaphore-bounded
        parser.parse(raw_text) -> Candidate | ParseFailure
        SpendTracker.record(cost) -> raises SpendLimitExceeded if daily cap hit
        Ledger.insert_candidate(candidate)                        # persisted before any filtering
    Ledger.insert_excavation_cycle(cycle_summary)
    return CycleResult(candidates, parse_failures, total_cost)
```

`SpendTracker` reads/writes a running daily total from `excavation_cycles` (sum of `total_cost_usd` for today) — no separate mutable counter, consistent with append-only design.

`ClaudeClient.generate` wraps `AsyncAnthropic.messages.create`, retries on rate-limit errors with exponential backoff (up to a configurable max), and requests JSON-structured output matching the `Candidate` fields per `General-spec.txt:468-481`.

---

## 6. CLI Surface (Step 1 stubs → Step 3 functional)

| Command | Step 1 (stub) | Step 3 (functional) |
|---|---|---|
| `hanno mine --seed <id> --candidates 50` | prints "not implemented" | runs `ExcavatorPipeline.run_cycle` |
| `hanno seeds add "<text>" --domain mathematics` | stub | `SeedManager.submit` |
| `hanno seeds list` | stub | `Ledger.list_seeds` with priority ordering |
| `hanno discoveries` | stub | stub until Step 6 |
| `hanno stats` | stub | basic cycle/cost stats from `excavation_cycles` |
| `hanno report` | stub | stub until Step 7 |
| `hanno export` | prints path | `Ledger.export_json` |

---

## 7. Data Flow, Steps 1–3 (concrete instance of General-spec Section 13, Stage 1 only)

```
config/default.yaml ──► HannoConfig ──► calibration_events (on diff)
                                   │
hanno seeds add ──► SeedManager.validate ──► Ledger.insert_seed + insert_seed_status_event(queued)

hanno mine --seed X --candidates 50
        │
        ▼
SeedManager.next_for_domain / explicit seed lookup
        │
        ▼
ExcavatorPipeline.run_cycle
    ├─ for each candidate slot: ClaudeClient.generate(temp, variant)
    ├─ parser.parse → Candidate | logged parse failure
    ├─ SpendTracker.record (halts cycle if daily cap exceeded)
    └─ Ledger.insert_candidate (immediate, pre-filtering)
        │
        ▼
Ledger.insert_excavation_cycle (cost, timing, pass/fail counts)
```

No fingerprinting, sieving, or verification happens yet — candidates sit in the ledger raw, exactly as Step 3's scope specifies.

---

## 8. Test Plan (Steps 1–3)

- **`test_ledger_append_only.py`**: for every table, attempt an `UPDATE` and a `DELETE` through raw SQL against the initialized schema and assert both raise. Also assert no `update_*`/`delete_*` method exists on `Ledger` (reflection-based check).
- **`test_config.py`**: load default config, mutate one YAML value, reload, assert exactly one new `calibration_events` row with correct old/new values.
- **`test_seed_validation.py`**: assert a vague seed ("interesting mathematics") is rejected with feedback; assert a well-formed seed passes; assert domain-keyword check works per domain.
- **`test_excavator_parser.py`**: feed known-good JSON, known-malformed JSON, and known-truncated output; assert correct `Candidate`/`ParseFailure` classification in each case. Include a temperature-1.2-style "drifted format" fixture per the strategy doc's explicit warning.

---

## 9. What Is Deliberately Deferred

- Fingerprint embedding/compression logic (Step 4) — table exists, no code.
- Sieve evaluation dimensions (Step 5) — table exists, no code.
- Lean verification (Step 6) — table exists, no code.
- Cartographer, reseeding automation, solid seeds — later steps / addenda, not touched.
- AI-based seed validation — explicitly deferred to Step 5 per strategy doc's own risk note; Step 2 uses rule-based checks only.

---

## 10. Immediate Blocking Dependency

Step 2's success criteria cannot be fully met without the **10-seed mathematics corpus** referenced as "tech spec Section 18," which is absent from `General-spec.txt`. Recommend authoring this corpus (10 seeds across number theory, algebra, topology, analysis, combinatorics, etc., per the sub-domains listed in `General-spec.txt:181-186`) as a short follow-up task before or during Step 2 implementation.
