# Excavator Migration Plan — from a monolithic prompt to a role-decomposed pipeline

Status: proposal. Grounded in (a) the failures observed in real runs (local
DeepSeek-R1-14B and Claude Haiku 4.5) and (b) a survey of 2025–2026 research
on agentic loops, diversity collapse, autoformalization faithfulness,
self-verification, and evolutionary math discovery. See "Research basis"
tags throughout and the Sources section at the end.

---

## 0. Diagnosis — what is actually broken

Five failures, all observed in real runs, not hypothesised:

1. **Goal drift.** On a seed asking to prove one concrete claim (`n^11 - n
   divisible by 66`), every candidate abandoned the task and reformulated
   into progressively more abstract objects (maximality theorems, Frobenius
   varieties, invented "Forced(P) density" statistics). Zero candidates
   attempted the actual question.
2. **Token burn.** Haiku spent ~60k input tokens per candidate, hitting the
   fixed 6-round tool cap on *every* candidate, re-billing the full growing
   conversation each round.
3. **Cannot solve a solvable seed.** 15 candidates, 3 iterations, ~$1, no
   discovery, on a problem a competent model solves in one prompt.
4. **Vapor scores as high as substance.** Empty "Cayley graph" tangents
   self-rated 0.65–0.85, the same band as rigorously-derived results.
5. **Faithfulness gaps.** A "verified" Lean statement had the divisibility
   direction backwards; another was satisfied by a vacuous `use 1` witness.
   The triviality filter cannot detect either.

**Root cause (single).** One model runs one prompt that simultaneously:
generates a claim, decides whether to explore/branch/stop, self-rates its own
novelty and rigor, and self-verifies via tools. Every failure above is a
symptom of that concentration.

**Research consensus (independent strands, same conclusion).** Goal-drift
work states it directly: *"if planning and execution share the same context
window and the same inference pass, goals become diluted."* Self-verification
work: intrinsic self-critique without external feedback often *degrades*
performance, and self-reported confidence is *anti-correlated* with
correctness. The proven discovery systems (FunSearch, AlphaEvolve) and provers
(DeepSeek-Prover-V2, VERITAS) all win by **decomposing the monolith into
separate specialized roles with external grounding**.

---

## 1. Design principles (each traces to research + an observed failure)

1. **Separate roles, separate context windows.** Planning, generation,
   formalization, criticism, and proving are distinct passes, not one prompt.
   *(goal drift; VERITAS role decomposition; AlphaEvolve generator/evaluator
   split)*
2. **Replace self-report with external signals.** Self-rated novelty/rigor
   are the least trustworthy inputs and are currently weighted heavily. Score
   on: numeric-check pass, formalizability, embedding-distance-to-corpus.
   *(self-verification unreliability; vapor-scoring failure)*
3. **Two missions, cleanly split.** EXPLORE (generate a diverse corpus,
   optimize for diversity + checkability) and SOLVE (prove/disprove one fixed
   claim, optimize for goal adherence) are different jobs with different
   prompts, loops, success criteria, and possibly different models.
   *(goal drift; the core "explore vs solve" conflict)*
4. **Faithfulness is a first-class gate.** Lean certifies well-typedness, not
   that the statement *means* the claim. Round-trip back-translation + an
   adversarial critic gate every statement before it is trusted.
   *(The Faithfulness Gap; backwards-divisibility and vacuous-witness bugs)*
5. **Decomposition beats one-shot proving.** Decompose a theorem into a
   sketch with `sorry` subgoals, solve each recursively. *(DeepSeek-Prover-V2;
   the n^7-n 42-case proof that stalled on the final branch)*
6. **Diversity is engineered, not hoped for.** Verbalized sampling
   (K candidates + probabilities in one call) and an island-model population
   replace the beam that tunnels into one idea. *(mode collapse / diversity
   collapse; the n^3-n 90-candidate loop)*
7. **Cost is a design constraint.** Dynamic turn limits, prompt caching,
   trajectory reduction, and per-role model routing, not fixed caps and
   full-conversation re-billing. *(token-burn failure)*
8. **The human owns novelty and final faithfulness; the system surfaces,
   never decides.** There is no oracle for "novel vs the literature" or for
   "this Lean means this English". The system maximizes support for those
   judgments; it does not automate them. *(honest limit — do not paper over)*

---

## 2. Target architecture — the roles

Each is a distinct call (often a distinct model), not a section of one prompt.

- **Orchestrator / Router.** Chooses EXPLORE vs SOLVE, manages the island
  population, applies dynamic turn limits, routes each role to an
  appropriate model (cheap/fast for breadth, strong/dedicated for depth).
  Hooks into the existing `client_factory.build_client` — extend it to take a
  `role` and return the right model per role.
- **Proposer** (EXPLORE only). Proposes claims using verbalized sampling for
  diversity. Never self-rates novelty; that is the Evaluator's job.
- **Formalizer.** Informal claim → Lean statement. Hard rule: preserve
  logical strength — do NOT existentially-quantify objects the claim names
  concretely, do NOT weaken hypotheses. (Today lives in
  `hanno/lean/autoformalize.py`; keep, tighten the prompt, make it a named
  role.)
- **Faithfulness Critic** (new). Back-translates the Lean statement → English,
  compares to the original claim; adversarially hunts the loophole ("argue
  this statement is weaker/different/vacuous vs the claim"). Separate from the
  Formalizer, which has an incentive to produce something easy to prove.
- **Evaluator** (replaces self-rated `path_score`). Scores on external
  signals only: numeric-check result, formalizes-or-not, and
  novelty-discounted distance to the explored corpus (via the existing
  `FingerprintEngine`, which is currently underused). No self-ratings.
- **Prover.** Faithful Lean statement → complete proof via subgoal
  decomposition. (Today `hanno/lean/prove.py` does one-shot whole-proof;
  upgrade to decompose → recurse.) Ideally a dedicated/stronger prover model.
- **Island population manager** (EXPLORE). Multiple isolated populations with
  periodic migration to prevent premature convergence; novelty-discounted
  fitness; exhaustion detection on *semantic movement*, not score.

---

## 3. What we keep, extend, or replace (strangler-fig, not rewrite)

| Component | Verdict | Note |
|---|---|---|
| Ledger (models/repository, append-only) | **Keep** | Add rows for per-role calls, back-translations, faithfulness verdicts |
| `FingerprintEngine` (embeddings) | **Keep + promote** | Becomes the novelty backbone; today only used for dup detection |
| `hanno/lean/checker.py` | **Keep** | Triviality filter is fine as one signal among several |
| `hanno/lean/autoformalize.py` | **Keep + tighten** | Becomes the Formalizer role; add strength-preservation rule |
| `hanno/lean/prove.py` | **Extend** | Add subgoal decomposition (DeepSeek-Prover pattern) |
| `hanno/verification/counterexample.py` | **Keep** | Good as-is; cheap refutation before expensive proof |
| `client_factory.build_client` | **Extend** | Add per-role model routing |
| Skills / tool loop (`client.py`) | **Keep + cost-control** | Dynamic limits, caching, trajectory reduction |
| Provider abstraction | **Keep** | Exactly what enables per-role routing |
| UI | **Keep + extend** | Surface faithfulness + per-role trace |
| **Monolithic excavator prompt** (`prompts.py`) | **Replace** | Split into role prompts; this is the root-cause change |
| **`compute_path_score`** (self-rating based) | **Replace** | External-signal Evaluator |
| **Beam search** (`run_iterative_mining`) | **Replace (EXPLORE)** | Island model; keep beam available as fallback |

The point: ~80% of the code is reusable. The migration strangles the
monolithic prompt and the self-rating score; it does not rewrite the system.

---

## 4. Migration phases (ordered by leverage-per-effort and dependency)

### Phase 0 — Instrumentation + a solvable-seed benchmark (do first)
**Status: SHIPPED.** Per-attempt `input_tokens`/`output_tokens` now flow
through `autoformalize_with_retry`, `prove_with_retry`, and
`search_for_counterexample` (previously untracked entirely). Fixed 5-case
fixture at `benchmarks/solve_benchmark.yaml`, run via `hanno benchmark`
(`hanno/cli/commands/benchmark.py`, `hanno/solve/benchmark.py`) — reports
solve-rate and real $ cost, the regression number every later phase is
judged against.

**Why first:** we got here by flying blind. Before changing behavior, make
success measurable.
- Per-role token/cost accounting in the ledger.
- A fixed benchmark set of seeds with *known* answers a competent model
  should solve (n^k−n family, sum of first n odds = n², n(n+1) even, …).
- A single metric: "of N solvable benchmark seeds, how many reach a genuine
  `VERIFIED` end-to-end, and at what $ cost." This is the regression number
  every later phase is judged against.
- **Effort:** small. **Risk:** none. **Unblocks:** everything.

### Phase 1 — SOLVE mode (the goal-drift fix)
**Status: SHIPPED (Faithfulness Critic still stubbed — see Phase 3).**
`hanno/solve/pipeline.py`'s `solve_claim()` takes one fixed claim through
formalize → (optional counterexample search) → prove, reusing the existing
ledger schema (a solve-mode "candidate" is a real `Candidate` row,
`prompt_variant="solve_mode_direct"`) so `hanno trace` and the mindmap render
it identically to an explore-mode result. Exposed via `hanno solve SEED_ID
--claim "..."`. `hanno/lean/autoformalize.py` also gained
`STRENGTH_PRESERVATION_RULES`, directly targeting two faithfulness bugs
observed in production (vacuous-witness existentials, reversed relation
direction) — this hardens both SOLVE and EXPLORE mode's autoformalize step.

**Highest leverage, lowest effort. Directly fixes today's disaster.**
- New `solve` path: given ONE claim, run Formalizer → Faithfulness Critic
  (stub in P1, real in P3) → Prover. No "be novel/non-obvious" instruction
  anywhere in it. Re-anchor the exact claim on every tool round.
- Tools in solve mode stress-test *this* claim only (numeric check, search
  for a counterexample), never propose new claims.
- **Success metric:** benchmark solvable-seed solve-rate goes from ~0 to high.
- **Research basis:** goal-drift structural separation + goal re-anchoring.
- **Effort:** medium. Reuses formalizer/prover/counterexample wholesale.

### Phase 2 — Tool-loop cost controls
**Status: SHIPPED.** All four items landed:
- **Native prompt caching (Anthropic):** `AnthropicClient.generate()`
  (`hanno/excavator/client.py`) now accepts `cache_prefix`; when the caller
  supplies a literal, byte-identical prefix of the prompt, it's split into
  its own content block with an `ephemeral` `cache_control` breakpoint.
  Wired from `ExcavatorPipeline` using `ALL_VARIANTS[arm.prompt_variant]`
  (`hanno/excavator/prompts.py`) — the ~1.3-1.4K-token per-variant
  instructions block that's identical across every candidate and seed for a
  given variant, for the whole life of the process. `OpenAICompatibleClient`
  accepts and ignores the same param (OpenAI's own API caches long stable
  prefixes automatically, no manual breakpoint in that wire format).
- **Duplicate-tool-call detection** (a variant of "trajectory reduction",
  retargeted from the originally-scoped "smaller round budget" once it was
  confirmed SOLVE mode's autoformalize/prove/counterexample legs never
  touch the tool loop at all — see below): both `AnthropicClient` and
  `OpenAICompatibleClient` now cache each unique `(tool name, canonical
  input)` pair per `generate()` call; a repeated identical call is served
  from cache instead of re-executed, with an explicit nudge fed back
  ("you already ran this, try something different or finalize"). Directly
  targets the observed Haiku pattern of 5-6 near-identical `run_code` calls
  per candidate.
- **Dynamic turn limits:** extends the duplicate-call cache with a
  `stale_rounds` counter (`AnthropicClient`/`OpenAICompatibleClient`) — if
  *every* tool call in a round was served from cache (zero genuinely new
  calls that round), it counts as stale; 2 consecutive stale rounds force
  finalization immediately via the same refusal mechanism `max_tool_rounds`
  exhaustion already used, instead of waiting out the rest of a much larger
  fixed budget.
- **Per-role model routing:** `ExcavatorConfig` gained optional
  `prover_provider`/`prover_model`/`prover_base_url` fields (all `None` by
  default — zero behavior change unless set); `client_factory.build_prover_client()`
  builds a second, separate client for the Prover role only when
  `prover_model` is configured. Wired into `hanno solve`, `hanno benchmark`,
  and `hanno discoveries verify` — lets a cheap/fast model handle
  formalization while a stronger, dedicated model handles proving.

Independent of P1; attacks the burn.
- Native prompt caching (Anthropic) on the stable prefix.
- Dynamic turn limits (stop when marginal tool yield drops) instead of a
  fixed 6-round cap hit every time.
- Trajectory reduction: summarize/trim prior tool results instead of
  re-sending the full growing conversation each round.
- Per-role model routing: cheap model for Proposer/breadth, strong only for
  Prover/Critic.
- **Success metric:** $/candidate down 40–75% at equal solve-rate.
- **Research basis:** dynamic turn limits (−24%), caching (−41–80%),
  trajectory reduction (−40%+).
- **Effort:** medium, mechanical.

### Phase 3 — Faithfulness critic (back-translation gate)
Prevents *false-positive discoveries* — the most dangerous output.
- Back-translate every Lean statement → English; compare to the original
  claim (embedding + LLM judge). Adversarial "find the loophole" pass for
  vacuous witnesses and weakened hypotheses.
- Nothing is recorded as a discovery until it passes; a mismatch is surfaced
  to the human, not silently dropped.
- **Success metric:** the two known bugs (backwards divisibility, `use 1`)
  are caught; no faithful statement is falsely rejected on the benchmark.
- **Research basis:** The Faithfulness Gap; round-trip verification & repair.
- **Effort:** medium. High safety value; discoveries are rare so cost is low.

### Phase 4 — External-signal Evaluator + novelty-discounted scoring
Replace the self-rated `path_score`.
- Score = f(numeric-check pass, formalizes-or-not, distance-to-explored-corpus
  via FingerprintEngine). Down-weight or drop self_rated_* entirely.
- Novelty discount: an idea near the explored centroid loses effective score,
  so restating the same formula stops being rewarded.
- Exhaustion detection watches *semantic movement*, not score plateau.
- **Success metric:** vapor tangents score low; duplicate-restatement rate on
  the n^3-n-style seed drops sharply.
- **Research basis:** self-verification unreliability; diversity collapse;
  typicality bias.
- **Effort:** medium; touches the scoring core.

### Phase 5 — Island-model EXPLORE search
Replace beam search for the explore mission.
- Multiple isolated populations + periodic migration (prevents the beam
  tunneling into one idea, then into an empty tangent).
- Verbalized sampling in the Proposer for intra-island diversity.
- Independent generation before context-sharing (Nominal Group Technique) —
  Hanno's current `already_explored`-sharing beam is the dense-topology
  anti-pattern the research warns accelerates convergence.
- **Success metric:** sustained semantic diversity across iterations; more
  distinct genuine directions per seed.
- **Research basis:** AlphaEvolve/FunSearch island model; verbalized sampling;
  diversity-collapse mitigations.
- **Effort:** high; the main structural change for explore.

### Phase 6 — Decomposition prover + dedicated prover model
**Status: SHIPPED**, plus one addition the DeepSeek-Prover-V2 pattern didn't
originally call for: a Mathlib lemma-grounding tool. Both landed together
after a real `hanno solve` run (Haiku, "n^13 - n divisible by 2730") failed
on two purely mechanical grounds despite choosing the textbook-correct
strategy (Fermat's Little Theorem per prime factor + CRT): it invoked a
hallucinated lemma name (`Nat.dvd_of_dvd_of_dvd_mul_left`, which does not
exist), and it burned the entire proof-retry budget regenerating the whole
combined proof from scratch on every failure instead of fixing the one bad
step.
- **`decompose_and_prove`** (`hanno/lean/prove.py`): when a proof attempt
  already typechecks in the `have h1 : S1 := by sorry ... have hN : SN := by
  sorry <combine>` sketch shape, each stub is solved independently via a
  *recursive* `prove_with_retry` call (small budget each) instead of
  regenerating the whole proof. Already-solved (non-stub) `have`s are left
  untouched but still passed as hypotheses to later subgoals that depend on
  them. Purely additive — falls back to today's exact whole-proof retry on
  any parse failure or unsolved subgoal, so a genuine one-shot proof attempt
  (no sketch shape) costs nothing extra.
- **`skills/mathlib_search.yaml`**: greps the real, vendored Mathlib source
  (`data/mathlib_cache/.lake/packages/mathlib/`, ~8-9K `.lean` files) for a
  lemma name or keyword phrase — `EXISTS` with the real signature, or `NOT
  FOUND` with the closest real declarations by keyword overlap, so the model
  can check a name before betting a proof attempt on it. Wired as a tool into
  both `autoformalize_with_retry` and `prove_with_retry` (previously neither
  supported tools at all — a confirmed gap, now closed for both SOLVE mode
  and `hanno discoveries verify`/`hanno lean-pilot`, which share the same
  functions).
- Route proving to a dedicated/stronger prover model, not the general model
  (Phase 2's per-role routing, above) — orthogonal to decomposition, ships
  alongside it.

**Follow-up round, after a live before/after test on the exact same claim
(Haiku, "n^13 - n divisible by 2730") confirmed the lemma-grounding fix
worked** (the model re-searched the previously-hallucinated lemma, found it
didn't exist, and used a real one instead) **but surfaced two adjacent
gaps**, both closed the same session:
- `mathlib_search` extended to also verify **tactic** names, not just
  lemmas — tactics are a different Lean declaration shape (`syntax`/`elab`/
  `macro ... : tactic`) that the original lemma-only scan couldn't see.
  Directly targeted a second hallucination on the same claim: `decide_mod_cast`,
  a tactic that doesn't exist, invisible to the original tool for exactly
  this reason.
- A known Mathlib community gotcha — natural-number subtraction not
  commuting with casts into another ring/type (`↑(a - b)` ≠ `↑a - ↑b` in
  general) — added as an explicit rule in `PROVE_PROMPT`/`RETRY_AFTER_ERROR_PROMPT`
  (`KNOWN_FORMALIZATION_GOTCHAS`), plus a targeted, pattern-detected hint
  (`Nat.ModEq`/`Nat.cast_sub`) injected into the retry prompt specifically
  when Lean's error text matches this failure shape. This is not a
  hallucination or a math error — the model's proof strategy was correct —
  it's a formalization-technique gap distinct from both prior categories.

**Second follow-up, after a live test on a genuinely fresh claim (Haiku,
"n^17 - n divisible by 510") confirmed BOTH prior fixes working** (zero
hallucinated lemmas or tactics across 34 real `mathlib_search` calls; the
`Nat.cast_sub`/`b ≤ a` pattern applied correctly, unprompted by an error,
for every prime factor) **but surfaced a fourth, distinct failure class**:
the model committed `omega` — a linear-arithmetic-only decision procedure —
to a sub-goal (`n ≤ n^17`) involving exponentiation, which omega structurally
cannot reason about at all, regardless of the fact being true. Not a
hallucination (the tactic is real); not the subtraction/cast gotcha (that
part was handled correctly); a real tactic committed to outside its own
power. Generalized rather than patched as a fourth special case:
- **`lean_goal_solver`** (`skills/lean_goal_solver.yaml`): a new grounding
  tool, the generic complement to `mathlib_search` — that tool grounds
  *name* lookup, this one grounds *tactic selection*. Given a standalone
  sub-goal, it tries a battery of standard closing tactics (`omega`,
  `decide`, `norm_num`, `simp`, `positivity`, `exact?`) via real Lean
  compilation, early-exiting on the first that works, and reports the
  verified truth either way — rather than the model guessing one tactic and
  betting a whole proof attempt on it. Doesn't require anticipating any
  specific "tactic X can't handle goal shape Y" trap; asks Lean's own
  automation directly instead.
- `TACTIC_SELECTION_GUIDANCE` added to `PROVE_PROMPT`/`RETRY_AFTER_ERROR_PROMPT`:
  don't commit a small arithmetic sub-goal to one guessed tactic — use
  Lean's `first | tac1 | tac2 | ...` combinator, or call `lean_goal_solver`
  first if available.
- (En route: fixed a related gap in `mathlib_search` itself — Lean's own
  trailing-`?` "search" tactics, `exact?`/`apply?`, didn't match the
  identifier regex, silently falling through to noisy fuzzy search instead
  of an exact match.)

- **Success metric:** benchmark seeds that stalled at the proof step (e.g.
  n^7-n) now reach `VERIFIED`.
- **Research basis:** DeepSeek-Prover-V2 (88.9% MiniF2F via subgoal
  decomposition); dedicated-prover-beats-general.
- **Effort:** high; benefits most from a strong model.

---

## 5. What this migration does NOT solve (state plainly)

- **Genuine novelty vs the literature.** No oracle exists in-system. The
  Evaluator measures novelty *relative to this run's corpus*, never relative
  to mathematics. A perfectly-executed pipeline can still emit a correct,
  well-known result. Novelty judgment stays human.
- **Whether the generator can produce genuinely new mathematics at all.** An
  open research question. This plan makes the pipeline *reliable, honest, and
  efficient*; it does not guarantee *discovery*. That ceiling is set by the
  generator model and by the (human) novelty judgment, not by the plumbing.
- Any design that claimed to close these automatically would be the most
  dangerous version of all — a confident false "VERIFIED + NOVEL".

---

## 6. Recommended first cut

**Phase 0 + Phase 1 + Phase 2.** Together they directly fix the two things
that are actively painful right now — the token burn and the inability to
solve a solvable seed — and Phase 0 gives the benchmark that every later phase
is measured against. Phases 3–6 are real projects that build on that
foundation, in that order (safety → quality → diversity → proving power).

Do not attempt all six at once. Each phase is independently shippable and
independently testable against the Phase 0 benchmark.

**Status update:** Phases 0–2 and 6 are now shipped (Phase 6 pulled forward
ahead of 3–5, driven directly by a real observed failure — see Phase 6 above).
Phases 3 (Faithfulness critic), 4 (external-signal Evaluator), and 5
(island-model EXPLORE search) remain. Phase 5 in particular is the main
remaining lever for EXPLORE-side power/diversity — everything shipped so far
improves SOLVE-mode efficiency and the shared verification backbone, not the
Excavator's own candidate-generation/exploration strategy.

---

## Sources

Agentic cost/efficiency: Goal Drift in LM Agents (arXiv:2505.02709),
Inherited Goal Drift (2603.03258), Trajectory Reduction (2509.23586),
Agent-Omit (2602.04284), CostBench (2511.02734).
Diversity: Verbalized Sampling (2510.01171), Diversity Collapse in
Multi-Agent LLM Systems (2604.18005).
Faithfulness: The Faithfulness Gap (2606.16541), Beyond Compilation
(2606.31002), ProofFlow (2510.15981).
Self-verification: ReVeal (2506.11442), VERITAS (2606.19399).
Evolutionary discovery: AlphaEvolve, FunSearch (PubMed 38096900),
CodeEvolve (2510.14150).
Proving: DeepSeek-Prover-V2 (2504.21801).
Interestingness/novelty: Judging What We Cannot Solve (2602.06291),
Learning Interestingness (2511.14778), Mining Math Conjectures (2412.16177).
