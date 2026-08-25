# Hanno — Project Overview

**What Hanno is:** an autonomous mathematical knowledge-mining pipeline. You
give it a seed idea (a direction, or a concrete claim), and it uses an LLM
plus Lean 4 (Mathlib) to generate candidate mathematical claims, formalize
them, verify them (or refute them), and surface genuinely proved, checkable
results for human review. Every step is recorded in an append-only ledger so
the whole process is auditable and reproducible.

This document explains the system end-to-end: architecture, data flow,
modules, and the CLI surface. For step-by-step run instructions, see
`@/Users/jounayd/PROJECTS/HANNO/MVP_USER_MANUAL.md`. For the plan driving
the Excavator's ongoing redesign, see
`@/Users/jounayd/PROJECTS/HANNO/EXCAVATOR_MIGRATION_PLAN.md`.

---

## 1. The two missions: EXPLORE vs SOLVE

Hanno has two distinct operating modes, because they are fundamentally
different jobs:

- **EXPLORE** (`hanno mine`) — given a seed *direction*, generate many
  diverse candidate claims, self-rate them, deduplicate them, and (in
  `--until-promising` mode) push survivors through the Lean triviality
  filter. Optimizes for **diversity + breadth**.
- **SOLVE** (`hanno solve`) — given exactly *one fixed claim*, formalize it
  faithfully, search for a counterexample, and if none is found, attempt a
  complete Lean proof. No candidate generation, no "be novel" instruction
  anywhere. Optimizes for **goal adherence + correctness**.

This split exists because running one prompt that both generates ideas *and*
tries to prove one fixed thing causes goal drift — see
`@/Users/jounayd/PROJECTS/HANNO/EXCAVATOR_MIGRATION_PLAN.md:11-44` for the
concrete failure this fixes.

---

## 2. End-to-end pipeline

```
 hanno seeds add                  hanno mine                    hanno discoveries verify
 ┌───────────┐   submit    ┌─────────────────┐   survives    ┌──────────────────────┐
 │   Seed    │ ──────────► │  ExcavatorPipeline│  triviality  │ Discovery (pending)  │
 │ (queued)  │             │  (EXPLORE mode)  │ ────────────► │                      │
 └───────────┘             └─────────────────┘   filter      └──────────────────────┘
       │                          │                                    │
       │ FingerprintEngine        │ FingerprintEngine                  │ counterexample
       │ (dedup seeds)            │ (dedup candidates)                 │ search, then
       ▼                          ▼                                    │ real proof search
 same-concept seeds        candidates persisted                        ▼
 flagged, never rejected   with duplicate_status,               VERIFIED / REFUTED /
                           path_score, exploration_signal        unresolved

                                            hanno solve <seed> --claim "..."
                                            ┌───────────────────────────────┐
                                            │ Formalize → Counterexample →  │
                                            │ Prove (SOLVE mode, one claim) │
                                            └───────────────────────────────┘
```

**Step by step:**

1. **Seed submission** (`hanno seeds add`) — a human (or an AI-suggested
   `--type ai-generated` seed from `mine`'s output) submits a concept. The
   `FingerprintEngine` checks it against all prior seeds for concept-level
   duplication (flags, never rejects).
2. **Excavation** (`hanno mine --seed ID`) — the `ExcavatorPipeline` builds
   a plan of "arms" (model, temperature, prompt variant, reasoning effort),
   calls the LLM in parallel, parses each response into a `Candidate`, and
   fingerprints it for duplicate detection. Optionally runs iteratively
   (`--depth`, beam search over the best candidates) or until a candidate
   independently clears the Lean triviality filter (`--until-promising`).
3. **Triviality filter** (`hanno/lean/checker.py`, invoked either inline
   during `--until-promising` or via `hanno lean-pilot`) — translates a
   candidate's claim to Lean and checks whether `exact?`/`simp` trivially
   closes it. Survivors (compile, non-trivial, unresolved) become
   `Discovery` rows.
4. **Deep verification** (`hanno discoveries verify`) — for each pending
   discovery: search for a numeric counterexample first (cheap, definitive
   rejection if found); if none is found, attempt a real Lean proof
   (`hanno/lean/prove.py`, with subgoal decomposition and Mathlib-grounding
   tools).
5. **SOLVE mode** (`hanno solve <seed_id> --claim "..."`) is an alternative
   entry point that skips generation/exploration entirely: it takes one
   fixed claim straight through formalize → counterexample → prove. Also
   used by `hanno benchmark` to score the system against a fixed
   solvable-seed set.

---

## 3. Module map

```
hanno/
├── ledger/        append-only SQLite store + schema + repository + JSON export
├── config.py      YAML config, env overrides, calibration-event logging
├── seeds/         seed validation, priority queue, lifecycle manager
├── fingerprint/   concept-level semantic dedup (embeddings + PCA + cosine sim)
├── excavator/     candidate generation pipeline (EXPLORE mode)
├── solve/         single-claim prove/disprove pipeline (SOLVE mode) + benchmark runner
├── lean/          autoformalization, triviality checker, proof search
├── verification/  counterexample search (numeric stress-testing)
├── skills/        registry + sandboxed runner for miner-callable tools
├── ui/            local FastAPI web UI (mining control, live trace, settings)
├── viz/           trace mindmap visualization (static HTML/JS)
└── cli/           Typer CLI entrypoint + one command module per subcommand
```

### 3.1 Ledger (`hanno/ledger/`)

- `schema.sql` — all tables, append-only via `AFTER UPDATE`/`AFTER DELETE`
  triggers that abort the statement. Status/counters are **event tables**
  (e.g. `seed_status_events`, `seed_stat_events`), never mutated columns —
  "current status" is derived by querying the latest event.
- Key tables: `seeds`, `candidates`, `excavation_cycles`, `fingerprints`,
  `lean_verifications`, `discoveries`, `tool_invocations`, `arm_score_events`,
  `reseed_events`, `calibration_events`.
- `repository.py` (`Ledger` class) — the only way any code touches the DB;
  insert + read-only methods per table, plus aggregate queries (arm stats,
  spend totals, lineage lookups).
- `export.py` — builds the JSON structure consumed by `hanno trace --json`
  and the mindmap viewer.

### 3.2 Config (`hanno/config.py`, `config/default.yaml`)

Pydantic-validated YAML config with env var overrides
(`HANNO_SECTION__KEY=value`). Every config load diffs against the last
snapshot stored in the ledger and logs a `calibration_event` per changed
value — full audit trail of every parameter change over the project's
history, not just what the current YAML says.

### 3.3 Seeds (`hanno/seeds/`)

- `validation.py` — rule-based checks (length, domain keywords). Concept-level
  duplicate detection is **not** done here — that's the Fingerprint Engine's
  job, scoped across all domains.
- `queue.py` — priority ordering: fertility (`discoveries_attributed /
  cycles_run`), recency boost, domain-balance penalty, cross-domain bonus.
- `manager.py` (`SeedManager`) — submission (wired to `FingerprintEngine`),
  lifecycle transitions (`active`, `exhausted`, `retired`), and
  `reseed_from_candidate` (spinning up a new seed from a candidate's
  `suggested_seeds`, tracking the reseed chain/depth).

### 3.4 Fingerprint Engine (`hanno/fingerprint/`)

Concept-level uniqueness, shared by seeds and candidates, so "the same idea
in different words" is detected regardless of phrasing:

1. **Embed** locally (`sentence-transformers/all-MiniLM-L6-v2` — no API
   cost).
2. **Reduce** dimensionality (raw truncation until enough samples exist,
   then PCA), 128-dim by default.
3. **Hash** the compressed vector (SHA-256) for cheap exact-duplicate
   lookups.
4. **Compare** by cosine similarity against every prior fingerprint in the
   same entity pool (`seed` vs `candidate`), classify `NOVEL` /
   `NEAR_DUPLICATE` / `EXACT_DUPLICATE` against calibratable thresholds.

Seeds are flagged, never rejected. Candidates are stored either way, tagged
with `duplicate_status`, and re-scored (an exact duplicate is zero-value
regardless of self-rated confidence).

### 3.5 Excavator (`hanno/excavator/`) — EXPLORE mode

- `prompts.py` — universal + math-specific prompt variants, each asking for
  a structured JSON candidate: `core_claim`, `formal_statement`, `intuition`,
  `connections`, `self_rated_novelty`/`rigor`, `numeric_check`,
  `exploration_signal` (`continue` / `branch_point` / `dead_end`),
  `suggested_seeds`.
- `client.py` / `client_factory.py` — provider-abstracted async client
  (`anthropic` or any `openai_compatible` backend — OpenAI itself, or a
  self-hosted server like Ollama/vLLM/LM Studio). Supports native prompt
  caching, tool/skill calling, duplicate-tool-call detection, and dynamic
  turn-limit cutoffs. `build_prover_client` allows routing the Prover role
  to a separate (e.g. stronger) model than the general Excavator model.
- `arm_selector.py` — chooses (model, temperature, prompt_variant,
  reasoning_effort) combinations per candidate, either round-robin or via a
  **UCB1 bandit** over historical `arm_score_events` (`hanno arms` shows the
  leaderboard).
- `spend_tracker.py` — hard daily spend cap, checked before every call.
- `parser.py` — defensive JSON parsing of LLM output.
- `pipeline.py` (`ExcavatorPipeline`) — orchestrates a cycle: builds the arm
  plan, generates candidates in parallel waves (feeding each wave the most
  recent claims so candidates diverge), fingerprints and scores every
  candidate, tracks exploration signals, and recommends reseeding when a
  seed looks exhausted (`dead_end_reseed_threshold`) or when a sustained
  strong path is found (iterative/`--depth` mode). Also supports
  `run_iterative_mining` — a beam search across iterations, refining the top
  candidates instead of re-mining from scratch, with configurable beam
  width, diversity constraints, and an optional "wildcat" thread that
  re-mines the original seed fresh each iteration to avoid tunneling.

### 3.6 Solve (`hanno/solve/`) — SOLVE mode

- `pipeline.py` (`solve_claim`) — the fixed-claim path: formalize (reusing
  `hanno/lean/autoformalize.py`, unchanged) → counterexample search → prove.
  Persists the claim as a real `Candidate` row (`prompt_variant =
  "solve_mode_direct"`) so existing tooling (`hanno trace`, the mindmap)
  renders it identically to an explore-mode result — no schema fork.
- `benchmark.py` — runs a fixed set of *known-solvable* seeds
  (`benchmarks/solve_benchmark.yaml`) through `solve_claim` and reports
  solve-rate + real dollar cost. This is the regression number every
  Excavator migration phase is judged against (`hanno benchmark`).

### 3.7 Lean (`hanno/lean/`)

- `autoformalize.py` — informal claim → Lean statement, with
  **strength-preservation rules**: don't existentially-quantify a concretely
  named object, don't weaken hypotheses (targets two observed faithfulness
  bugs: a vacuous `use 1` witness, and a backwards divisibility direction).
- `checker.py` — typechecks Lean code via `lake env lean` against a local
  Mathlib cache; `classify_result` maps outcomes to `UNTRANSLATABLE` /
  `MALFORMED` / `TIMEOUT` / `KNOWN` (trivially closed) / `None` (a genuine
  survivor — compiles, non-trivial, unresolved).
- `prove.py` — real proof search with retry-on-error, plus
  `decompose_and_prove`: when a proof sketch already has the `have hN : SN
  := by sorry` shape, each stub is solved independently via a recursive
  call instead of regenerating the whole proof from scratch on every
  failure (DeepSeek-Prover-V2 pattern). Both formalize and prove accept
  grounding tools (`mathlib_search`, `lean_goal_solver`, see below) so the
  model can check a lemma/tactic name or verify a sub-goal against a real
  compile before committing to it.

### 3.8 Verification (`hanno/verification/`)

- `counterexample.py` — asks the model to write a Python stress-test
  program for a claim, runs it through the sandboxed `run_code` skill, and
  looks for an explicit `COUNTEREXAMPLE FOUND:` marker. A real counterexample
  is a definitive, immediate rejection — cheaper than a full proof attempt
  and needs no human judgment call.

### 3.9 Skills (`hanno/skills/`, `skills/*.yaml`)

Miner-callable tools, defined declaratively in YAML (name, description,
shell command, input mode, timeout, output cap, enabled flag) and exposed to
the model as Anthropic-style tools during generation. `hanno/skills/registry.py`
loads and validates them; `runner.py` executes them as subprocesses.

Shipped skills:

| Skill | Purpose | Enabled by default |
|---|---|---|
| `run_code` | Arbitrary Python execution for self-testing ideas | Yes (contained, not sandboxed — see file header) |
| `mathlib_search` | Grounds lemma/tactic names against real Mathlib source | Yes |
| `lean_goal_solver` | Tries standard closing tactics on a stuck sub-goal via real Lean compilation | Yes |
| `graceful_check` | Verifies a graceful tree labeling (stdlib-only, safe) | Yes |
| `sympy_eval` | SymPy/Python execution via `exec()` | No — explicit opt-in |
| `tree_lab` | Search/generation instruments for graceful-labeling research | No |

`hanno skills` lists all of them (including disabled) so what's exposed to
the model is never a mystery.

### 3.10 UI (`hanno/ui/`)

A local FastAPI app (`hanno ui`) serving a static frontend
(`hanno/ui/static/`) for: starting/monitoring mining jobs, managing API
keys/settings, and a live trace/mindmap view of a seed's candidates and
lineage. `jobs.py` tracks running mining jobs per seed (prevents double-starts);
`secrets.py` handles API key storage/loading.

### 3.11 CLI (`hanno/cli/`)

Entrypoint `hanno/cli/main.py` registers every subcommand. `context.py`'s
`build_context()` is the shared bootstrap (load config → init ledger schema
→ log calibration diffs → build `FingerprintEngine` → load skills) used by
every command.

---

## 4. CLI command reference

| Command | Purpose |
|---|---|
| `hanno seeds add TEXT --domain D [--type T] [--submitter S]` | Submit a seed |
| `hanno seeds list [--domain D]` | List the seed priority queue |
| `hanno mine --seed ID [--candidates N] [--depth N] [--top-k N] [--fresh] [--until-promising]` | Run EXPLORE mode (single cycle or iterative beam search) |
| `hanno solve SEED_ID [--claim "..."]` | Run SOLVE mode on exactly one claim |
| `hanno benchmark` | Run the fixed solvable-seed benchmark, report solve-rate + cost |
| `hanno lean-pilot [--limit N]` | Run the Lean triviality filter over already-mined candidates |
| `hanno discoveries list` | List discoveries (candidates that survived the triviality filter) |
| `hanno discoveries verify [--limit N]` | Deep-verify pending discoveries: counterexample search, then real proof search |
| `hanno trace SEED_ID [--full] [--limit N] [--lineage ID] [--json PATH]` | Full mining trace for a seed — every prompt/response, tool call, and score |
| `hanno arms` | Leaderboard of (model, temperature, variant, reasoning_effort) by avg path_score |
| `hanno skills` | List registered miner tools (enabled + disabled) |
| `hanno stats` | Basic cycle/cost statistics |
| `hanno export [--path PATH]` | Dump the entire ledger to JSON |
| `hanno ui [--host H] [--port P]` | Start the local web UI |
| `hanno report` | Stub — full reports arrive later |

---

## 5. Data flow through the ledger (why append-only)

Nothing is ever updated or deleted. A seed's "status" is not a column — it's
derived from the latest row in `seed_status_events`. A candidate's
duplicate classification, path score, and Lean verification result are all
separate rows keyed to the candidate. This means:

- The full history of every decision (including config changes via
  `calibration_events`) is always reconstructible.
- Nothing needs a migration to "fix" a stored status — you insert a new
  event.
- `hanno export` / `hanno trace --json` can always produce a complete,
  faithful snapshot for external tooling (the mindmap viewer, the UI).

---

## 6. Status of the Excavator migration plan

The Excavator (EXPLORE mode) has been under active redesign per
`@/Users/jounayd/PROJECTS/HANNO/EXCAVATOR_MIGRATION_PLAN.md`, moving from a
monolithic "generate + self-rate + self-verify in one prompt" design toward
role-decomposed, externally-grounded pipeline stages. Current state:

- **Phase 0 (benchmark + instrumentation) — shipped.**
- **Phase 1 (SOLVE mode) — shipped** (`hanno solve`, `hanno/solve/pipeline.py`).
- **Phase 2 (cost controls: caching, dedup-tool-call detection, dynamic turn
  limits, per-role model routing) — shipped.**
- **Phase 6 (decomposition prover + Mathlib/tactic grounding tools) —
  shipped**, pulled forward ahead of 3–5 after real observed proof failures.
- **Phase 3 (faithfulness critic / back-translation gate) — not yet built.**
- **Phase 4 (external-signal Evaluator replacing self-rated `path_score`) —
  not yet built.**
- **Phase 5 (island-model EXPLORE search replacing beam search) — not yet
  built.** This is the main remaining lever for EXPLORE-side diversity —
  everything shipped so far improves SOLVE-mode efficiency and the shared
  verification backbone, not the candidate-generation/exploration strategy
  itself.

See `@/Users/jounayd/PROJECTS/HANNO/EXCAVATOR_STRATEGY_AND_CAPACITY_ANALYSIS.md`
for a deeper capacity/strategy analysis, and
`@/Users/jounayd/PROJECTS/HANNO/LOCAL_MODEL_SETUP.md` for running against a
self-hosted model instead of the Anthropic API.

---

## 7. What is explicitly NOT automated (by design)

- **Novelty vs the published literature.** The Fingerprint Engine only
  measures novelty relative to this run's own corpus, never against all of
  mathematics. A correct, well-known result can still come out the other
  end. This judgment stays human.
- **Faithfulness certainty.** Lean certifies that a statement type-checks,
  not that it *means* the original English claim. The Faithfulness Critic
  (Phase 3) is designed to narrow this gap but is not yet built — treat
  every `VERIFIED` result as "a proof of *some* Lean statement", and read
  the statement before trusting it means what the claim says.
- **Discovery of genuinely new mathematics.** The pipeline makes the process
  reliable, honest, and efficient. Whether the underlying LLM can produce
  genuinely new mathematics at all is an open question this plumbing does
  not answer.
