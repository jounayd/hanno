# Project Hanno — MVP Build Strategy

**Version 1.0 — June 2026**

Step-by-step build strategy for the Hanno MVP prototype. Each step has its own scope, deliverables, success criteria, and honest risk assessment. Follow the order. Do not skip ahead.

---

## The Strategy in One Paragraph

Build the mine before the market. The MVP proves one thing only: that the pipeline can take a seed, generate candidates, filter them, verify them in their domain, and record everything permanently. Mathematics first because it has the strongest automated verification. Science and philosophy after the pipeline is proven. Everything economic — tokens, NFTs, solid seed royalties, validator payments — is explicitly out of scope until the mine demonstrably finds gold.

---

## Build Order Overview

| Step | Component | Duration | Depends On |
|------|-----------|----------|------------|
| 1 | Foundation: Config + Ledger | Week 1 | Nothing |
| 2 | Seed Management | Week 2 | Step 1 |
| 3 | Excavator (Mathematics only) | Weeks 3-4 | Steps 1-2 |
| 4 | Fingerprint Engine | Weeks 5-6 | Steps 1-3 |
| 5 | Sieve | Weeks 7-9 | Steps 1-4 |
| 6 | Lean Verifier (Mathematics) | Weeks 10-12 | Steps 1-5 |
| 7 | Reporting + Human Review Loop | Week 13 | Steps 1-6 |
| 8 | First Validation Run (Math) | Weeks 14-17 | All above |
| 9 | Science Domain Expansion | Weeks 18-20 | Step 8 validated |
| 10 | Philosophy Domain Expansion | Weeks 21-23 | Step 8 validated |
| 11 | Cartographer + Reseeding | Weeks 24-26 | Steps 8-10 |
| 12 | Full 30-Day Mining Campaign | Weeks 27-31 | Everything |

Total: roughly 7 months to a demonstrable, multi-domain, validated system.
If you need to compress: Steps 9-11 can run in parallel with Step 8's validation run.
Compressed timeline: 4.5-5 months.

---

## Step 1 — Foundation: Configuration + Ledger

### Purpose
Everything else writes to the Ledger and reads from Config. Build the ground truth layer first so every subsequent component has a place to record its activity from day one.

### Scope
- Configuration system: all parameters from the tech spec Section 14, loaded from a single config file, overridable by environment variables
- Ledger: SQLite database with all tables from tech spec Section 12
- Append-only enforcement at the application layer: no update or delete operations exposed anywhere in the codebase
- Basic structured logging: every module logs to both console and file
- Export function: full Ledger dump to JSON on demand

### Deliverables
- Config file with every parameter documented inline
- Database with all tables created: seeds, candidates, fingerprints, collision_events, sieve_evaluations, lean_verifications, science_verifications, philosophy_verifications, discoveries, human_reviews, calibration_events, reseed_events
- CLI skeleton with all commands stubbed: `mine`, `seeds`, `discoveries`, `stats`, `report`, `export`
- A test that proves append-only enforcement works: attempting an update or delete through any exposed interface must fail

### Success Criteria
- Can insert and query every record type
- Export produces valid JSON containing all records
- Config changes are logged as calibration events automatically
- Zero code paths that mutate existing records

### Honest Risks
Low risk step. The temptation is to over-engineer the database schema. Resist. SQLite with the spec's schema is enough. Do not add indexing optimizations, sharding logic, or blockchain-preparation abstractions. Plain tables, plain queries.

---

## Step 2 — Seed Management

### Purpose
Seeds are the input to everything. Build the seed queue, validation, and lifecycle before the Excavator exists so the Excavator has something real to consume on day one.

### Scope
- Seed submission: manual entry via CLI
- Seed validation: specificity check, domain assignability check, duplicate similarity flagging (basic text similarity for now — real fingerprint similarity comes in Step 4)
- Seed queue: priority ordering by fertility score, recency, and domain balance
- Seed lifecycle states: queued, active, exhausted, retired
- Fertility tracking fields (populated later once discoveries exist)

### Deliverables
- `hanno seeds add "seed text" --domain mathematics` works end to end
- `hanno seeds list` shows queue with priorities and states
- Seed validation rejects vague seeds with useful feedback ("too broad: specify a sub-domain or specific structure")
- The 10 mathematics seeds from tech spec Section 18 loaded as the starting corpus

### Success Criteria
- All 10 math corpus seeds load and validate
- A deliberately vague seed ("interesting mathematics") is rejected with feedback
- Queue ordering respects priority rules
- State transitions are recorded as events, not field mutations

### Honest Risks
Seed validation via AI evaluation adds an API dependency this early. Acceptable simplification for MVP: validate with rule-based checks only (length bounds, domain keyword presence) and defer AI-based validation to Step 5 when the Sieve infrastructure exists. Flag this as a known simplification.

---

## Step 3 — Excavator (Mathematics Only)

### Purpose
The generation engine. Mathematics only at this stage — one domain, done properly, before expansion.

### Scope
- Parallel candidate generation against the Claude API
- Four temperature settings: 0.3, 0.7, 1.0, 1.2
- Five universal prompt variants + three mathematics-specific variants (per tech spec Section 5)
- Structured output parsing: every candidate must yield core claim, formal statement, intuition, connections, self-assessed novelty, identified weaknesses
- Candidate validation: minimum/maximum length, required fields present
- Immediate persistence: raw candidates hit the Ledger before any filtering
- Rate limit handling with exponential backoff
- Hard daily spend limit enforced in code

### Deliverables
- `hanno mine --seed <id> --candidates 50` produces 50 structured candidates in the Ledger
- All 8 prompt variants implemented and selectable
- Parallel execution working (configurable max concurrent calls, default 10)
- Cost tracking per cycle logged to the Ledger

### Success Criteria
- 50 candidates generated in under 10 minutes
- Over 90% of raw outputs parse successfully into the structured format
- Spend limit halts generation when reached (test this deliberately)
- Every candidate traceable to its seed, temperature, and prompt variant

### Honest Risks
Structured output parsing will fail more than expected. Models drift from requested formats, especially at temperature 1.2. Mitigation: request JSON output explicitly, parse defensively, and log unparseable outputs rather than discarding silently — they are data about prompt quality. Expect to iterate on prompts for a full week beyond initial implementation.

---

## Step 4 — Fingerprint Engine

### Purpose
The most technically original component and the core anti-duplication mechanism. Semantic hashing of ideas so the same idea in different words is caught.

### Scope
- Local sentence embedding model (no API dependency — runs on your hardware)
- Fixed embedding model choice, documented and locked for the MVP lifetime
- Dimensionality reduction to 128 dims (raw truncation below 500 stored fingerprints, fitted reduction after)
- SHA-256 hashing of the compressed vector
- Cosine similarity against all stored fingerprints
- Three-way classification: NOVEL / NEAR_DUPLICATE (>= 0.92) / EXACT_DUPLICATE (>= 0.99)
- Full vector storage alongside compressed vector (enables future re-compression)

### Deliverables
- Every candidate automatically fingerprinted after generation
- Collision events recorded to Ledger for every check
- Calibration-phase handling: system knows when it has too few fingerprints to trust reduction
- A dedicated test suite (this component needs it most)

### Success Criteria — test these explicitly
- Identical text → identical hash, every time
- Same mathematical idea paraphrased three different ways → NEAR_DUPLICATE or EXACT_DUPLICATE classification
- Two clearly different conjectures → NOVEL classification
- Reduction refit at 500 and every 1000 fingerprints does not change classifications of previously stored pairs (spot check 20 pairs after refit)

### Honest Risks
The similarity thresholds (0.92 / 0.99) are educated guesses. They will be wrong in one direction or the other. Build a threshold audit command — `hanno fingerprint audit` — that shows recent NEAR_DUPLICATE pairs side by side so you can eyeball whether the threshold is catching real duplicates or flagging distinct ideas. Budget calibration time in Step 8. Also: mathematical notation embeds poorly in general-purpose sentence embedders. If collision detection performs badly on formal statements, fall back to fingerprinting the natural-language core claim only, and document the change.

---

## Step 5 — Sieve

### Purpose
Multi-dimensional filtering. This is where most candidates die, and where evaluation budget is protected by strict ordering.

### Scope
- Five dimensions per tech spec Section 7: Coherence (0.25), Novelty vs. literature (0.25), Significance (0.25), Fertility (0.15), Formalizability (0.10)
- Strict cost ordering with short-circuits: coherence first (fail below 0.50 → stop), literature novelty second (fail below 0.30 → stop), then the rest
- External literature integration: arXiv and Semantic Scholar queries, abstract embedding, similarity comparison against candidate fingerprint
- Weighted final score, pass threshold 0.65 (configurable)
- Full reasoning strings stored for every dimension of every evaluation

### Deliverables
- Full sieve runs automatically on all non-duplicate candidates
- Every evaluation stored with scores and reasoning
- `hanno stats` shows pass rates by dimension — which dimension kills the most candidates
- Calibration hooks: thresholds and weights adjustable via config, changes logged

### Success Criteria
- Sieve pass rate lands in the 15-35% band on the starting corpus (outside this band → thresholds need adjustment before proceeding)
- Fail reasons are interpretable: reading 10 random failures, you should agree with at least 7
- Short-circuiting works: incoherent candidates cost exactly one AI call
- Literature check finds obviously-known results: seed the test with a known theorem stated as a candidate — it must fail the novelty dimension

### Honest Risks
Two real risks. First, AI-as-judge is inconsistent — the same candidate evaluated twice can score differently. Mitigation: temperature 0 for all sieve evaluations, and accept residual variance as noise. Second, the literature novelty check has a blind spot: much mathematics lives in books and journals not indexed by arXiv. The sieve will pass some known results. This is why human review (Step 7) is mandatory before any novelty claim. Never let the sieve's novelty score be presented as a novelty guarantee.

---

## Step 6 — Lean Verifier (Mathematics)

### Purpose
The trustless verification layer for mathematics and the hardest component in the build. A statement that type-checks in Lean 4 against Mathlib is formally well-defined — no human judgment required.

### Scope
- Lean 4 + Mathlib installation, documented step by step (this alone takes a day)
- Lean server mode from the start — per-file compilation is unusably slow
- Translation pipeline: candidate formal statement → Lean 4 syntax via AI translation with structured prompt
- Retry loop: up to 3 attempts, feeding Lean errors back to the translator each time
- Triviality check: is the statement a direct restatement of a single Mathlib lemma?
- Five-way classification: VERIFIED / KNOWN / UNTRANSLATABLE / MALFORMED / TIMEOUT
- All attempts recorded, including failures — failure patterns drive prompt improvement

### Deliverables
- `hanno verify --candidate <id>` runs the full translation-and-check loop
- Automatic verification of sieve passes when `--verify` flag is set on mining
- Documented Lean setup guide (you will need it again on any new machine)
- Translation failure pattern log: recurring error types surfaced weekly

### Success Criteria
- At least 1 in 10 sieve-passing candidates reaches VERIFIED within three translation attempts
- UNTRANSLATABLE is correctly distinguished from MALFORMED (spot check: UNTRANSLATABLE items should read as coherent math that Mathlib simply lacks vocabulary for)
- Server mode keeps per-check time under 90 seconds after warm-up
- Zero false VERIFIED results: manually inspect every VERIFIED item in the first month

### Honest Risks
This is the highest-risk step in the entire build. Translation failure rates of 50-70% on first attempt are normal and expected. Three specific warnings. First: do not let this step block the pipeline — the system must be able to mint discoveries with UNVERIFIED tiers while Lean integration matures. Second: budget real time here; if Step 6 takes four weeks instead of three, that is the plan working, not failing. Third: a Lean VERIFIED result means well-typed and non-trivial, not true and not novel. Keep that distinction sharp in every report the system generates.

---

## Step 7 — Reporting + Human Review Loop

### Purpose
The system's outputs meet human judgment here. Without this step you have a pipeline that produces claims nobody has checked.

### Scope
- Discovery report: all discoveries with full provenance chain (seed → candidate → sieve → verification → discovery)
- Pipeline statistics report per tech spec Section 11
- Human review queue: discoveries sorted by sieve score, formatted for expert review with suggested related literature
- Review capture: verdict (NOVEL / KNOWN / UNCERTAIN / REJECTED), significance rating 1-5, notes, suggested follow-up seeds
- Sieve calibration from review data: compare expert verdicts to sieve scores, adjust weights, log every adjustment

### Deliverables
- `hanno report` generates a complete markdown discovery report
- `hanno review queue` outputs the review queue in a format you can send to a mathematician
- `hanno review submit` captures expert verdicts into the Ledger
- Calibration routine that runs after every 10 reviews

### Success Criteria
- A domain expert with no knowledge of the system can read the review queue output and evaluate items without asking clarifying questions
- Every discovery's full provenance is reconstructable from the report alone
- After the first calibration pass, sieve scores correlate better with expert verdicts than before (measure this)

### Honest Risks
The real risk here is not technical — it is finding the mathematician. Start recruiting your first expert reviewer during Step 5, not Step 7. You need someone with graduate-level mathematics who will review 20-30 candidates for a few hours of paid work. Without this person, Step 8 cannot produce validated results, and every claim the system makes remains unchecked.

---

## Step 8 — First Validation Run (Mathematics)

### Purpose
The moment of truth for the core thesis. Four weeks of real mining on the mathematics corpus, ending with expert-reviewed results you can stand behind.

### Scope
- Continuous mining across all 10 mathematics corpus seeds
- Minimum 40 cycles (roughly 2000 candidates)
- Lean verification on all sieve passes
- Expert review of every minted discovery
- Threshold calibration at week boundaries
- Full statistics documentation

### Deliverables
- Complete campaign report: candidates generated, pass rates per stage, discoveries minted, verification outcomes, expert verdicts
- The honest headline numbers, whatever they are
- A written assessment: does the pipeline work, and what are its measured failure modes

### Success Criteria — the real ones
- At least 1 discovery that an expert reviewer marks NOVEL with significance ≥ 3
- Complete audit trail for every discovery
- Measured (not estimated) rates for every pipeline stage
- Sieve calibrated against at least 20 expert reviews

### Honest Risks
This step may partially fail, and that must be acceptable. If 40 cycles produce zero expert-confirmed novel discoveries, the result is diagnostic data, not proof the concept is dead: it tells you whether the problem is seed quality, sieve calibration, excavator prompts, or the harder possibility that novelty at expert level needs more scale. Do not proceed to Steps 9-12 by momentum if Step 8 produces nothing — stop, diagnose, fix, rerun. One genuine confirmed discovery changes everything. Zero means iterate, not decorate.

---

## Step 9 — Science Domain Expansion

### Purpose
Second domain, exercising a completely different verification model: falsifiability analysis instead of formal proof.

### Scope
- Science-specific excavator prompt variants (mechanistic hypothesis, unification proposal, prediction generation)
- Science verifier per tech spec Section 8: falsifiability analysis, literature consistency check (PubMed + Semantic Scholar + arXiv), theoretical framework connection, novelty of implied experiment
- Six-way classification: VERIFIED / CONTRADICTED / REDUNDANT / UNFALSIFIABLE / DISCONNECTED plus malformed handling
- The 10 science corpus seeds from tech spec Section 19 loaded
- Recruit a second expert reviewer with natural science background

### Success Criteria
- Science pipeline runs end to end on the science corpus
- At least one candidate reaches VERIFIED classification
- CONTRADICTED classification demonstrably works: a candidate contradicting known experimental results must be caught
- Expert review of first science discoveries completed

### Honest Risks
Science verification is softer than Lean. Falsifiability analysis is AI judgment, not formal proof, so the VERIFIED label means something weaker here — flag this difference in every report. Expect a higher REDUNDANT rate than in math: science literature is vast and the excavator will regenerate known hypotheses often. This is normal.

---

## Step 10 — Philosophy Domain Expansion

### Purpose
Third domain. Argument reconstruction and validity analysis — the domain most dependent on human review.

### Scope
- Philosophy-specific excavator variants (thought experiment, conceptual distinction, formal argument)
- Philosophy verifier per tech spec Section 8: argument reconstruction into premises and conclusion, validity check, consistency check, equivocation check, comparison against a curated database of named existing positions
- The named-positions database: start with ~100 canonical positions across the sub-domains in scope, expand continuously
- The 10 philosophy corpus seeds from tech spec Section 20 loaded
- Recruit a third expert reviewer with analytic philosophy background
- Hard rule enforced in code: no philosophy discovery is ever reported as novel without human review

### Success Criteria
- Argument reconstruction produces readable premise-conclusion structures an expert can evaluate directly
- RESTATEMENT detection works: feed the system a paraphrase of a famous position (e.g., standard compatibilism) — it must be caught
- At least one candidate reaches VERIFIED and survives expert review

### Honest Risks
Expect the highest restatement rate of all three domains — philosophy's position space is densely mapped and the excavator will rediscover known positions constantly. The named-positions database is the critical asset and it will be incomplete forever; treat gaps found during review as database growth, not system failure.

---

## Step 11 — Cartographer + Reseeding

### Purpose
The learning layer. Now that real exploration data exists across three domains, build the map and the mechanism for going deeper on productive paths.

### Scope
- Cartographer per tech spec Section 10: fingerprint clustering, cluster quality metrics, under-explored region identification, seed suggestion generation, bottleneck flagging
- Reseeding per Addendum 1: manual operator reseeding first, automatic signal-based reseeding second, chain depth limit of 3, full lineage tracking
- Cartographer-triggered reseeding stays disabled until 20+ cycles of map data exist

### Success Criteria
- Cartographer identifies at least 3 under-explored regions and its generated seed suggestions are meaningfully distinct from existing seeds (human judgment call — read them)
- Manual reseed mid-run works: inject a seed during an active cycle, confirm it runs with HIGH priority in the next pass
- Automatic reseeding fires only when 2+ signal conditions coincide, and every reseed's lineage chain is fully reconstructable
- A verification pass triggers a reseed (the strongest signal, per spec)

### Honest Risks
Automatic reseeding thresholds cannot be calibrated without Step 8's baseline data — this is why the step sits here and not earlier. Watch for reseed chains converging on local maxima: a chain that produces increasingly similar candidates is drilling a dry hole. The chain depth limit of 3 exists for exactly this reason; do not raise it because a chain "feels promising."

---

## Step 12 — Full 30-Day Mining Campaign

### Purpose
The demonstration run. All three domains, all mechanisms active, thirty continuous days, ending with the evidence package that decides whether the whitepaper ships.

### Scope
- All domains active with balanced queue allocation
- Reseeding active (manual + automatic)
- Cartographer guiding seed suggestions
- Weekly expert review sessions across all three reviewers
- Weekly calibration passes
- Complete campaign documentation

### Deliverables
- The evidence package: total candidates, per-stage rates per domain, all discoveries with provenance, all expert verdicts, all calibration history, total cost
- A per-discovery dossier for every expert-confirmed novel discovery: statement, formal statement, seed lineage, verification record, expert notes
- The go/no-go assessment for the whitepaper

### Success Criteria — the whitepaper bar
- At least 3 expert-confirmed novel discoveries across the three domains
- At least 1 Lean-VERIFIED mathematics discovery confirmed novel by expert review
- Reproducible pipeline: same seed + same config produces statistically similar pass rates across repeated cycles
- Honest cost accounting: total spend and cost per confirmed discovery, published as-is

### Honest Risks
The temptation at this stage is narrative inflation — presenting UNVERIFIED-tier discoveries as verified, or UNCERTAIN expert verdicts as confirmations. The entire value of this project depends on not doing that. Three genuinely confirmed discoveries with airtight provenance beat thirty inflated claims. If the campaign produces fewer than the target, report what it produced and extend the campaign. The evidence package is the foundation everything else gets built on — the whitepaper, the funding conversations, the credibility of every future claim. It has to be bulletproof.

---

## Cross-Cutting Rules for the Whole Build

**Rule 1 — The Ledger is never bypassed.** Every component writes its activity to the Ledger. If it is not in the Ledger, it did not happen.

**Rule 2 — Failures are data.** Every parse failure, translation failure, and rejection is logged with full context. Failure patterns drive the next iteration.

**Rule 3 — No novelty claims without human review.** The sieve suggests. Verification confirms well-formedness. Only expert review confirms novelty. The system enforces this distinction in every output it generates.

**Rule 4 — Spend limits are hard limits.** Every step that calls an API respects the daily cap in code, not in intention.

**Rule 5 — Calibration is logged.** Every threshold change, every weight adjustment, every prompt revision is a calibration event in the Ledger with its motivating data.

**Rule 6 — When a step fails its success criteria, stop.** Diagnose, fix, and re-run before proceeding. A pipeline built on an unvalidated component produces unvalidatable results.

---

## What You Will Have at the End

A single-machine system that takes seed concepts in three domains, generates candidates at scale, deduplicates them semantically, filters them across five dimensions, verifies them by domain-appropriate methods, records everything in an append-only ledger, learns which territory is fertile, deepens exploration on productive paths, and produces expert-reviewed discoveries with complete provenance.

That system — plus its evidence package — is the Satoshi moment. The whitepaper describes something that already works. Everything after that is scaling.