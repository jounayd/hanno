# Excavator Strategy & Capacity Analysis

**Purpose:** a complete, evidence-grounded assessment of the Excavator's current capacity to SOLVE and EXPLORE, what has actually been tried, what the real numbers say, what research has been leveraged, and what remains — as a single reference document, not scattered across session transcripts.

**Scope:** covers everything on record in `data/hanno.db` as of this writing (seeds submitted 2026-07-16 through 2026-07-24), the full `EXCAVATOR_MIGRATION_PLAN.md` history, and the underlying research survey that informed it.

---

## 1. Executive summary

Hanno's Excavator has two structurally distinct missions that were originally fused into one prompt, which was the root cause of the worst failures on record:

- **EXPLORE mode** — generate novel candidate mathematical claims from a seed concept, self-score them, and flag promising ones for verification. This is the actual mission-critical capability (Claim 1 of the project: "AI models can produce candidate novel ideas at scale").
- **SOLVE mode** — given one fixed, already-stated claim, formalize it in Lean 4 and either prove it, refute it with a counterexample, or honestly report "unresolved." Built this session specifically as a diagnostic/regression tool to isolate whether failures come from the *generation* step or the *verification* step.

**Where things stand, in one paragraph:** EXPLORE mode's original failure (documented below) was catastrophic goal drift — every candidate on a concrete claim abandoned it for fabricated abstractions. That specific failure is fixed by architectural separation (SOLVE mode exists and reliably stays on-claim). The verification backbone (formalize → counterexample → prove) is now real, honest, and — after two rounds of live testing tonight — measurably more capable than it was six hours ago: it no longer silently discards a model's real work due to two separate parsing bugs, and it no longer burns a whole proof-retry budget on one bad lemma name when four of five sub-steps were fine. What remains unproven is whether the *generation* side (EXPLORE mode itself) can produce anything a human would call genuinely novel — no seed run so far has produced a candidate that cleared even the self-reported novelty bar convincingly, and there is no external novelty check (Sieve, Phase 4) in the system at all yet.

---

## 2. System architecture as it exists today

```
[Seed] ──┬──► EXPLORE mode (hanno mine)
         │      Excavator.pipeline: build_prompt() → ModelClient.generate()
         │      → parse_candidate_json() → compute_path_score() (self-rated)
         │      → FingerprintEngine (dedup only, not novelty-vs-literature)
         │      → optionally: Lean triviality check (--until-promising)
         │
         └──► SOLVE mode (hanno solve)
                solve_claim(): autoformalize_with_retry() → check_statement()
                → (optional) search_for_counterexample()
                → prove_with_retry() [+ decompose_and_prove()]
                → Ledger (Candidate, LeanVerification, Discovery rows)
```

Both modes converge on the same Lean verification backbone (`hanno/lean/autoformalize.py`, `hanno/lean/prove.py`, `hanno/lean/checker.py`, `hanno/verification/counterexample.py`) — every fix to that backbone benefits both missions. `hanno discoveries verify` (EXPLORE's deep-verification step for `--until-promising` survivors) and `hanno lean-pilot` also share these same functions.

### 2.1 The model-client layer

- `hanno/excavator/client.py` (`AnthropicClient`) / `hanno/excavator/openai_client.py` (`OpenAICompatibleClient`) — one `ModelClient` protocol, two backends. `OpenAICompatibleClient` covers OpenAI itself plus any self-hosted server speaking the same wire format (this project uses it for a local Ollama instance running `deepseek-r1-14b-hanno`).
- Both implement an agentic tool loop: the model can call registered skills (`hanno/skills/`) mid-generation, get results fed back, up to `max_tool_rounds`.
- Cost controls now in both: duplicate-tool-call caching (a repeated identical call is served from cache, not re-executed), and dynamic early-stop (`stale_rounds` — 2 consecutive rounds with zero genuinely new calls force finalization before the fixed round budget is exhausted).
- Anthropic-only: prompt caching (`cache_prefix`) on the byte-identical per-variant instruction block, which is the same across every candidate for a given prompt variant for the life of the process.
- Optional per-role routing: `excavator.prover_model` (+ `prover_provider`/`prover_base_url`) lets the Prover step use a different, e.g. stronger, model than the one used for formalizing/proposing. Off by default.

### 2.2 EXPLORE mode internals (`hanno/excavator/pipeline.py`, `prompts.py`, `arm_selector.py`)

- **Prompt variants** (`prompts.py`): 5 universal + 3 math-specific framings (`direct_conjecture`, `framework_proposal`, `connection_discovery`, `adversarial_insight`, `open_problem_framing`, `proof_technique_proposal`, `counterexample_construction`, `generalization`). Each is a large (~1.3-1.4K token) fixed instruction block plus a shared JSON output-format spec, with the seed text and steering hint appended at the end.
- **Arm selection** (`arm_selector.py`): UCB1 bandit over (temperature × prompt_variant × reasoning_effort) — not over models. There is no per-arm model selection; model is fixed per run via config.
- **Diversity mechanisms**: `already_explored` (prior core_claims fed back so candidates diverge), `steering_hints` (10 rotating strategic angles — density argument, algebraic framing, computational angle, etc. — to force structural variety beyond just re-wording the same idea), `wave_size` (optional staged parallelism for real intra-cycle diversity at the cost of wall-clock time), beam diversity filtering by fingerprint cosine similarity for iterative mining.
- **Scoring** (`compute_path_score` in `scoring.py`): a function of **self-rated** novelty, self-rated rigor, numeric-check pass/fail, and exploration signal. This is the biggest known structural weakness — see §5.

### 2.3 SOLVE mode internals (`hanno/solve/pipeline.py`, shipped this session)

`solve_claim()` takes one fixed claim (never regenerated, never reformulated) through:
1. `autoformalize_with_retry()` — informal claim → Lean 4 statement, up to `max_translation_attempts` retries with real Lean error feedback.
2. `check_statement()` — does it survive the triviality filter (`exact?`/`simp`)?
3. (if a `run_code` skill is available) `search_for_counterexample()` — cheap, cannot confirm, can only reject.
4. `prove_with_retry()` (+ `decompose_and_prove()`) — the actual abstract, universally-quantified proof search against Lean's real kernel. This is the only step that can produce a genuine `VERIFIED` result.

No "be non-obvious" instruction anywhere in this path. This is the direct fix for goal drift (§3).

---

## 3. The original failure — full diagnosis (for the record)

On a seed asking the Excavator to investigate a single concrete claim (`n^11 - n divisible by 66`), **every candidate abandoned the actual question** and drifted into progressively more abstract, self-invented territory: a fabricated "maximality theorem," an invented algorithmic-complexity bound, a fictitious "Frobenius-Invariant Divisor" framework with made-up density statistics. Fifteen candidates, real API spend, zero attempts at the stated claim.

**Root cause (single):** one model, one prompt, doing four jobs simultaneously — generate a claim, decide whether to explore/branch/stop, self-rate its own novelty and rigor, and self-verify via tools. The standing EXPLORE prompt explicitly instructs "state a claim that is non-obvious... connects to existing knowledge in a non-trivial way" — this structurally rewards drifting away from a concrete, given task, because staying close to the literal claim reads as insufficiently "non-obvious."

**Contributing failures observed in the same incident:**
- **Token burn**: Haiku spent ~60K input tokens per candidate, hitting the fixed 6-round tool cap on every candidate, re-billing the full growing conversation each round.
- **Vapor scoring**: empty tangents self-rated 0.65–0.85 — the same band as rigorously-derived results. Self-reported confidence was uncorrelated with actual correctness.
- **Faithfulness gaps** (separate from goal drift, found later): a "verified" Lean statement had a divisibility direction backwards; another was satisfied by a vacuous `∃C, ...` witness (`C=1` trivially divides everything). The triviality filter cannot detect either — Lean certifies well-typedness, not that a statement *means* the original claim.

This is what `EXCAVATOR_MIGRATION_PLAN.md` Phase 0 diagnosis is built on, and it is a single research finding replicated independently across several literatures: *"if planning and execution share the same context window and the same inference pass, goals become diluted"* (goal-drift literature); self-verification without external grounding degrades performance and self-reported confidence is anti-correlated with correctness (self-verification literature); the working discovery systems (FunSearch, AlphaEvolve) and provers (DeepSeek-Prover-V2, VERITAS) all win by decomposing the monolith into separate specialized roles with external grounding, not by prompting harder.

---

## 4. Full chronological empirical record

All numbers below are pulled directly from `data/hanno.db` at the time of writing.

### 4.1 Seeds submitted, in order

| Seed (short) | Date | Type | Subject |
|---|---|---|---|
| `9ad8ed4d`, `4249c16e`, `72b4ac0b` | 07-16 | human | Erdős–Straus conjecture (3 separate submissions) |
| `00794ba2` | 07-17 | human | Graceful Tree Conjecture (Ringel–Kotzig) |
| `3830c151` | 07-17 | human | Graceful labelings, bandwidth ≤ 2 |
| `7be363a6` | 07-18 | human | Finite simple groups, bounded-degree embeddings |
| `cbddcf40` | 07-18 | human | Epistemic justification / infinite regress (philosophy) |
| `1b394369`, `8ab47cc8`, `e17ed9cc`, `e7450340` | 07-19 | human/derivative | UI reseed-feature verification seeds |
| `81f5273e` | 07-22 | human | `n³ - n` divisible by 6 |
| `c79667b9` | 07-22 | human | `n⁵ - n` divisible by 30 |
| `9a034ecc` | 07-22 | human | `n⁷ - n` divisible by 42 |
| `1093987a` | 07-23 | human | `n¹¹ - n` divisible by 66 (**the goal-drift disaster seed**) |
| `ffd907bc`, `f1ff0466` | 07-23 | human | `n¹³ - n` divisible by 2730 (SOLVE vs EXPLORE comparison pair) |
| `c6070d08` | 07-24 | human | `n¹⁷ - n` divisible by 510 (fresh control claim, untouched before tonight's fix-verification round) |

### 4.2 Cost and volume, by day

| Date | Cycles | Candidates requested | Parsed OK | Cost (Anthropic-priced) | Input tokens | Output tokens |
|---|---|---|---|---|---|---|
| 07-16 | 10 | 58 | 53 | $2.30 | 202,312 | 112,542 |
| 07-17 | 18 | 90 | 54 | $13.64 | 2,603,927 | 388,555 |
| 07-18 | 1 | 1 | 0 | $0 | 0 | 0 |
| 07-20 | 2 | 6 | 0 | $0 | 0 | 0 |
| 07-21 | 8 | 24 | 14 | $0.56 | 39,137 | 29,436 |
| 07-22 | 61 | 183 | 144 | $0 (local model) | 532,027 | 369,935 |
| 07-23 | 24 | 72 | 16 | $5.82 | 1,249,400 | 146,254 |

**Total historical spend on record: ~$22.3.** The 07-17 day alone (Erdős–Straus / graceful-tree seeds, pre-dating this session's fixes) burned $13.64 on 2.6M input tokens for only 54 successfully parsed candidates out of 90 requested — a ~$0.25/candidate cost with a ~40% parse-failure rate, the clearest historical evidence of the token-burn problem before Phase 2 cost controls existed.

### 4.3 Model comparison (all candidates on record)

| Model | Candidates | Parsed OK | Parse rate | Avg input tokens | Avg output tokens |
|---|---|---|---|---|---|
| `deepseek-r1-14b-hanno` (local) | 216 | 165 | 76% | 2,859 | 2,011 |
| `claude-sonnet-4-5` | 155 | 107 | 69% | 19,624 | 3,504 |
| `claude-haiku-4-5` | 21 | 21 | 100% | 82,884 | 9,275 |

Two things stand out. First, **DeepSeek's parse rate is the best of the three** despite being the smallest/free model — its failures are almost entirely JSON-formatting slips (below), not conceptual ones. Second, **Haiku's per-candidate token volume is enormous** (82,884 input tokens average) — this is almost entirely the tool-loop re-billing problem from the original diagnosis (§3); the 21 Haiku candidates on record are dominated by the disaster-seed run and tonight's SOLVE-mode tests, both of which are unusually tool-heavy compared to a normal EXPLORE candidate.

### 4.4 Why candidates fail to parse (234 failures on record)

| Cause | Count |
|---|---|
| JSON decode error: "Expecting value" (empty/malformed response) | 34 |
| JSON decode error: unquoted property name | 23 |
| API auth failure ("Could not resolve authentication") | 11 |
| Connection error | 7 |
| JSON decode error: invalid `\escape` | 10 (5+5) |
| JSON decode error: unterminated string | 4 |
| Missing required JSON fields | 3 |
| JSON decode error: invalid control character | 1 |

**None of these are reasoning failures.** They are entirely output-formatting fragility, concentrated on the local DeepSeek model (which doesn't reliably emit clean JSON without markdown wrapping or stray reasoning text) and transient auth/connection issues. This is a real, addressable efficiency gap distinct from anything in the Migration Plan — see §6.3.

### 4.5 Lean verification cost (the dominant wall-clock cost in the whole system)

84 Lean verifications on record, **49.5 minutes of cumulative real compile time**, broken down:

| Result | Count | Avg time | Max time |
|---|---|---|---|
| `None` (survived — non-trivial, a real candidate) | 25 | 95.6s | 163.4s |
| `KNOWN` (closed trivially by `exact?`/`simp`) | 2 | 104.0s | 123.9s |
| `MALFORMED` (compile error) | 38 | 17.4s | 56.8s |
| `UNTRANSLATABLE` | 19 | — (no compile attempted) | — |

**Every single Lean invocation costs 15–165 seconds of wall clock, dominated by `import Mathlib`'s elaboration cost, not the actual claim being checked.** This is the single largest fixed cost per attempt in the entire pipeline, and it applies identically regardless of model, provider, or claim complexity. It is untouched by any fix shipped this session — see §6.1 for the concrete lever (a persistent Lean server / REPL instead of one `lake env lean` subprocess per check) that the original tech spec already anticipated but was never built.

### 4.6 Tool usage (skills)

| Skill | Invocations | Avg duration |
|---|---|---|
| `run_code` | 266 | 549ms |
| `mathlib_search` | 29 | 1,561ms |
| `tree_lab` | 28 | 288ms |
| `graceful_check` | 1 | 38ms |

`run_code` dominates by volume (counterexample search + general verification). `mathlib_search`, added this session, is already the second-most-used tool despite being brand new — direct evidence that models reach for it readily once offered.

### 4.7 The `n^k - n` benchmark family — a direct efficiency/capability ladder

This family (`n³-n÷6`, `n⁵-n÷30`, `n⁷-n÷42`, `n¹¹-n÷66`, `n¹³-n÷2730`, `n¹⁷-n÷510`) is the closest thing on record to a controlled experiment: same underlying proof structure (Fermat's Little Theorem per prime factor, combined via CRT) at increasing difficulty (more prime factors to handle). Every SOLVE-mode attempt on `n¹³-n÷2730` on record:

| Candidate | Model | Formalize | Final outcome |
|---|---|---|---|
| `ef0ffb54` | DeepSeek | survived (117s) | killed mid-run (predates this session's fixes) |
| `d46ec245` | Haiku | survived (98s) | interrupted before prove step |
| `7d1fc92a` | Haiku | survived (108s) | **exhausted, 49s** — real CRT proof attempt, hallucinated lemma `Nat.dvd_of_dvd_of_dvd_mul_left` |
| `dd2f2795` | DeepSeek | survived, 2 attempts (110s) | exhausted, no code ever recorded — **later root-caused**: DeepSeek fences code as `` ```lean4 ``, not `` ```lean ``, silently discarded by the original regex |
| `f706a947` | DeepSeek | survived, 1st try (129s) | exhausted — after the `lean4`-fence fix, confirmed via direct replay that DeepSeek *was* producing valid, parseable proofs; the specific final attempt still failed on apparent genuine difficulty |
| `44244611` | Haiku | survived, 1st try (104s) | exhausted, 54s — **28 real `mathlib_search` calls**, correctly avoided the previously-hallucinated lemma, used the real `IsCoprime.mul_dvd` instead, but hit a *new* hallucinated tactic (`decide_mod_cast`) and a natural-subtraction/cast defeq mismatch |
| `77eba0e8` | Haiku | (in progress at time of writing) | — |
| `6dbec4e8` (fresh claim, `n¹⁷-n÷510`) | Haiku | survived (90s) | (in progress at time of writing) |

**This is the single clearest before/after evidence trail in the project.** Three structurally distinct hallucination classes were found and fixed in sequence, each one only visible because the *previous* fix let the model's real work through far enough to expose the *next* problem: (1) hallucinated lemma name → `mathlib_search` for lemmas; (2) silently-discarded valid proofs due to a fence-tag mismatch → regex fix; (3) hallucinated tactic name → `mathlib_search` extended to tactics; (4) a genuine Lean formalization gotcha (natural-subtraction/cast) → an explicit prompt rule plus a pattern-detected retry hint. None of these four are the same bug; each was invisible until the prior one was fixed.

### 4.8 Explore-mode scoring signal quality

| `numeric_check_result` | Candidates | Avg `path_score` | Avg self-rated novelty | Avg self-rated rigor |
|---|---|---|---|---|
| `None` (no witness attempted/applicable) | 233 | 0.33 | 0.52 | 0.69 |
| `0` (witness computed, claim FALSE) | 33 | 0.00 | 0.25 | 0.40 |
| `1` (witness computed, claim TRUE) | 19 | 0.38 | 0.31 | 0.54 |

The numeric-check gate is working as designed (a failed witness zeroes the score outright). But note self-rated rigor stays fairly high (0.40) even on candidates whose own witness proved the claim FALSE — self-report remains a soft, gameable signal exactly as diagnosed in §3, even where a hard signal (the numeric check) is available and should dominate.

---

## 5. Research survey — what was found and what was actually adopted

A dedicated research pass (12 targeted searches, 4 batches, covering agentic-loop efficiency, diversity collapse, autoformalization faithfulness, self-verification, and evolutionary discovery systems) grounds `EXCAVATOR_MIGRATION_PLAN.md`. Full citation list is in that document's Sources section. Mapping research → what shipped:

| Research finding | Adopted as |
|---|---|
| Goal drift when planning/execution/self-rating share one context (Goal Drift in LM Agents, 2505.02709; Inherited Goal Drift, 2603.03258) | **SOLVE mode** — structural separation, no "be novel" instruction |
| Self-reported confidence anti-correlated with correctness (self-verification literature: ReVeal 2506.11442, VERITAS 2606.19399) | Diagnosed but **not yet fixed** — `compute_path_score` still weights self-rated novelty/rigor heavily (Phase 4, unshipped — see §7) |
| Trajectory reduction cuts agentic-loop cost 40%+ (2509.23586) | **Duplicate-tool-call caching** + **stale-rounds early stop** |
| Prompt caching cuts cost 41-80% | **`cache_prefix`** on the byte-identical per-variant instruction block |
| Dynamic turn limits cut cost ~24% (implied by CostBench, 2511.02734; Agent-Omit, 2602.04284) | **`stale_rounds` threshold** (2 consecutive stale rounds force finalization) |
| Verbalized Sampling / diversity collapse in multi-agent systems (2510.01171, 2604.18005) | Diagnosed, **partially mitigated** (steering hints, `already_explored`) but the full island-model replacement for beam search (Phase 5) is **not shipped** |
| The Faithfulness Gap — Lean certifies well-typed, not "means the claim" (2606.16541) | `STRENGTH_PRESERVATION_RULES` (no vacuous existentials, preserve relation direction) — a **partial** fix; the full back-translation adversarial critic (Phase 3) is **not shipped** |
| DeepSeek-Prover-V2 — subgoal decomposition, 88.9% MiniF2F (2504.21801) | **`decompose_and_prove`** — solve each `have`-sketch stub independently instead of regenerating the whole proof |
| Dedicated/stronger prover model beats general model | **`prover_model` per-role routing** (built, not yet exercised in a real comparison run) |
| Judging What We Cannot Solve / Learning Interestingness (2602.06291, 2511.14778) — novelty/interestingness signals | **Not adopted** — there is still no external novelty-vs-literature check anywhere in the system |

**Honest gap:** three of the eight research-backed directions (Phase 3 faithfulness critic, Phase 4 external-signal evaluator, Phase 5 island-model search) remain unshipped. These are precisely the three that bear most directly on whether EXPLORE mode can be trusted to (a) not fabricate a false-but-plausible discovery, (b) not reward vapor, and (c) actually explore rather than converge on one idea. Phase 6 (decomposition prover) was pulled forward ahead of these three specifically because it had the most concrete, immediate evidence behind it (tonight's live n^13/n^17 tests) — a leverage call, not a claim that it was more important in the abstract.

---

## 6. What is actually slowing the Excavator down — concrete, ranked

### 6.1 Lean compile time (dominant, ~50 min cumulative, untouched)
Every check pays `import Mathlib`'s full elaboration cost (15-165s) as a subprocess invocation (`lake env lean <scratch-file>`), regardless of claim complexity. The original tech spec already names the fix: **a persistent Lean server / REPL process that keeps Mathlib's environment loaded in memory**, checking each new statement as an incremental addition rather than re-elaborating the whole import from a cold process every time. This alone would likely cut the ~50 minutes of cumulative compile time on record by an order of magnitude. Not built — `hanno/lean/checker.py`'s own docstring flags this explicitly: *"Per-file subprocess calls against a precompiled Mathlib cache, not the REPL/server mode the config anticipates for the eventual real pipeline — that's the natural next step if this pilot's numbers justify continuing."* The numbers now clearly justify continuing.

### 6.2 Decomposition's ledger blind spot (a real observability gap found tonight)
`decompose_and_prove`'s recursive sub-goal solving (each its own model call + Lean compile) writes **nothing** to the ledger until the entire top-level `prove_with_retry` call returns. During tonight's live monitoring, this made a genuinely-working decomposition attempt indistinguishable from a hung process for several minutes at a time — real work was happening, but there was no way to see it without external process/network inspection (`nettop`, `sample`). A cheap fix: have `decompose_and_prove` write a lightweight progress row (or emit a log line) per sub-goal solved, not just at the very end.

### 6.3 DeepSeek's JSON-formatting fragility (24% of its own candidates, cheap to fix)
216 DeepSeek candidates, 51 parse failures, dominated by "Expecting value" / unquoted-property-name / invalid-escape JSON errors — not reasoning failures. This is a known, well-documented failure mode of instruction-following in smaller/local models around strict JSON output, and the standard fixes (a JSON-repair pass before giving up, or switching to a more permissive extraction strategy, or explicit few-shot JSON examples in the prompt) are not currently applied to EXPLORE-mode candidate parsing (`hanno/excavator/parser.py`) the way `ensure_mathlib_import` and the `lean4`-fence fix already patch analogous fragility on the Lean side.

### 6.4 No per-role model routing exercised yet
The infrastructure (`prover_model`) is built and tested but has not yet been used in a real run to route proving to a stronger model than proposing. Given §4.7's finding that Haiku's proof *strategy* is reliably correct but its Lean/Mathlib API knowledge is the bottleneck, and Sonnet is already used successfully elsewhere in this project's history (§4.3), routing Haiku (or DeepSeek) for formalize/propose and Sonnet specifically for `prove_with_retry` is a directly testable, low-effort next experiment.

### 6.5 No external novelty signal (the deepest, least-addressed gap)
Every "discovery" on record — all 11 rows in the `discoveries` table — has `verification_result = 'SURVIVED'`, meaning it cleared the *triviality* filter, not a novelty filter. There is no check anywhere against arXiv, Semantic Scholar, or any external literature source. `compute_path_score` still leans on self-rated novelty even though §4.8 shows that signal doesn't reliably move with hard evidence. This is Phase 4 of the migration plan, entirely unbuilt.

---

## 7. Recommendations, ranked by leverage-per-effort

1. **Persistent Lean server (§6.1).** Highest leverage of anything in this document — cuts the single largest fixed cost in the system, benefits every mode and every future phase equally, and was already scoped in the original tech spec. Effort: medium-high (requires adopting Lean's REPL/server protocol instead of shelling out per-check), but touches only `hanno/lean/checker.py`'s internals — no API changes needed elsewhere.
2. **Decomposition progress visibility (§6.2).** Cheap, mechanical, directly improves operability of a feature already shipped tonight.
3. **Run the per-role routing experiment (§6.4).** Infrastructure exists; this is a single test run (`prover_model: claude-sonnet-4-5-...` while keeping formalize on Haiku/DeepSeek) against the exact `n^k-n` ladder already in §4.7, directly answering whether the bottleneck is model *capability* or model *strategy*.
4. **DeepSeek JSON-repair pass (§6.3).** Mechanical, bounded scope, would reclaim roughly a quarter of DeepSeek's wasted candidates for free.
5. **Phase 4 — external-signal Evaluator (§6.5 / migration plan).** Highest-leverage *unshipped* item for EXPLORE mode specifically — without it, nothing in the system can distinguish a genuinely novel candidate from a correct-but-well-known one, which is the actual mission (Claim 1/2 of the project). Higher effort than the above (touches the scoring core and needs a real literature-search integration), but it is the item most directly blocking "can Excavator explore, not just solve."
6. **Phase 5 — island-model EXPLORE search.** The remaining, largest structural change for exploration quality specifically (as opposed to solving efficiency, which items 1-4 mostly address). Correctly sequenced last: no point diversifying candidate generation until there's an external novelty signal (item 5) to tell diversified candidates apart from noise.

---

## 8. What this document does not claim

- It does not claim EXPLORE mode is currently capable of producing genuinely novel mathematics — no evidence on record supports or refutes that either way, because no external novelty check has ever run.
- It does not claim the `n^k-n` family is representative of harder, more open-ended claims — it was chosen specifically because it is checkable and difficulty-laddered, which makes it useful for isolating infrastructure bugs (exactly what happened tonight), not because it is a stand-in for genuine research-level difficulty.
- Every specific number in §4 is a direct SQL query result against `data/hanno.db` at the time of writing, not an estimate.
