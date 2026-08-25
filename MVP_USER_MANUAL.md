# Hanno MVP — User Manual

**Version:** 0.1.0 (Steps 1–4 implemented)  
**Scope:** Ledger, Seed Management, Excavator (mathematics), and Fingerprint Engine.

---

## 1. What the MVP can do

The MVP is a working knowledge-mining pipeline:

1. **Submit a seed** — a short concept/direction in a domain.
2. **Detect concept-level duplicates** — the Fingerprint Engine compares the *meaning* of a seed or candidate against everything already stored, even if the wording is different.
3. **Mine candidates** — the Excavator asks Claude to generate candidate ideas from a seed.
4. **Track everything** — all records are append-only in an SQLite ledger, with costs, cycles, and duplicate counts logged.
5. **Export / inspect** — dump the ledger as JSON or view basic stats.

**What is *not* in this build:** the Sieve AI scoring, domain verifiers (Lean 4 for math, literature checks for science/philosophy), discovery minting, and the reporting/review loop. Those arrive in Steps 5–7.

---

## 2. Installation

Requires **Python 3.11+**.

```bash
cd /Users/jounayd/PROJECTS/HANNO

# Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

After installation the `hanno` command is available:

```bash
hanno --help
# or, without activating the venv:
.venv/bin/hanno --help
```

---

## 3. Quick start

### 3.1 Set your API key

Mining uses the Anthropic Claude API, so export your key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

All fingerprinting/embeddings run locally and do **not** require an API key.

This is the default (`excavator.provider: anthropic`). To point Hanno at OpenAI itself, a self-hosted model (Ollama, LM Studio, ...), or any other server speaking the OpenAI-compatible API, set `excavator.provider: openai_compatible` and `excavator.base_url` instead — see the Configuration table below and `LOCAL_MODEL_SETUP.md` for a full local walkthrough. Skills work on both providers, subject to whether the specific model you point at supports tool-calling.

### 3.2 Add a seed

```bash
hanno seeds add \
  "Explore whether every finite simple group embeds into a bounded-degree symmetric group." \
  --domain mathematics \
  --submitter alice
```

If the seed is accepted, you will see its generated `seed_id`:

```
OK: seed 018f3e... queued.
```

If the wording is a near/exact duplicate of an existing seed, it is still queued but you get a note:

```
NOTE: NEAR_DUPLICATE of seed 018f3e...
```

### 3.3 List the seed queue

```bash
hanno seeds list
hanno seeds list --domain mathematics
```

Output columns:

- `status` — `pending`, `exhausted`, `retired`, etc.
- `cycles` — how many excavation cycles have run.
- `candidates` — candidates produced for the seed.
- `discoveries` — always 0 in the MVP (no verifier yet).

### 3.4 Mine candidates from a seed

```bash
hanno mine --seed <SEED_ID> --candidates 50
```

Only `--domain mathematics` is implemented right now. A typical run ends with:

```
cycle complete: requested=50 ok=47 failed=3 exact_dup=2 near_dup=1 cost=$0.4231 tokens_in=38214 tokens_out=22860
```

If candidates signal that the seed has branched into distinct directions, or that it's run dry, you'll also see:

```
exploration signals: branch_point=2 dead_end=1
SUGGESTED FOLLOW-UP SEEDS (review before submitting):
  - Explore the residue classes of n mod 24 that still lack a parametric construction.
  - Investigate whether Egyptian fraction identities generalize to 5/n.
RESEED RECOMMENDED: 2 candidate(s) identified distinct branching directions — review suggested_seed_texts before continuing to mine this seed.
```

These are recommendations only — nothing is auto-submitted. Review the suggested text and run `hanno seeds add` yourself for any worth pursuing.

### 3.5 Check stats, the arm leaderboard, and export

```bash
hanno stats
hanno arms
hanno export
```

`export` writes a timestamped JSON dump of the entire ledger to `data/exports/`.

`hanno arms` shows which (temperature, prompt_variant, reasoning_effort) combinations are actually producing good candidates, ranked by average `path_score`:

```
avg_score  pulls   temp  reasoning  variant
    0.812      6   1.0        deep  counterexample_construction (claude-sonnet-4-5-20250929)
    0.640      4   0.7    standard  proof_technique_proposal (claude-sonnet-4-5-20250929)
    0.210      5   0.3    standard  direct_conjecture (claude-sonnet-4-5-20250929)
    ...
```

### How candidate selection adapts over time

Each candidate now self-rates two things (`self_rated_novelty`, `self_rated_rigor`, both 0.0-1.0), and Hanno combines those with the fingerprint duplicate status and exploration signal into one `path_score` per candidate (0.0 for an exact duplicate, halved for a dead end, otherwise the average of the two self-ratings).

Instead of blindly round-robining through every (temperature, prompt_variant) combination, `hanno mine` uses a UCB1 bandit: it always tries every untried combination at least once, then increasingly favors whichever combinations have the highest average `path_score` — while never fully abandoning the less-proven ones. This is what `arm_selection_strategy: ucb1` (the default) does; set it to `round_robin` in the config to go back to the old exhaustive behavior.

`reasoning_efforts: ["standard", "deep"]` adds a second lane: "deep" arms use Claude's real extended-thinking budget (`deep_thinking_budget_tokens`, default 4096) instead of just a higher temperature. Because the API requires a fixed sampling temperature when extended thinking is enabled, "deep" arms always run at temperature 1.0 and cost more tokens per call (`deep_reasoning_max_tokens`, default 8000) — the bandit has to earn that cost back with a higher score, or it'll naturally get explored less over time.

**Cost note:** the first cycle(s) against a fresh ledger cold-start by trying every arm once — including all the pricier "deep" arms — before any adaptive favoring kicks in. Expect the first `hanno mine` run's average per-candidate cost to be higher than later runs on the same seed. Cold-start arm order is diagonal across (temperature, variant), so even a small `--candidates` count samples more than one temperature instead of exhausting every variant at 0.3 first.

### Avoiding identical reframings within one cycle

On a heavily-studied seed (e.g. a famous open conjecture), every prompt variant can independently reach for the same well-known approach — real testing showed 10 candidates in one cycle nearly all converging on the same residue-class construction. Two mechanisms address this:

- **`steering_hint`** (always on, no extra cost): a second, independent axis from `prompt_variant` — a rotating list of ~10 strategic angles (density/probabilistic argument, algebraic-geometric framing, computational search, cross-conjecture transplant, adversarial counterexample search, etc.) assigned to each candidate slot in the cycle. This forces structural diversity by construction, since it costs nothing in latency (it's just added prompt content) — `hanno trace` shows which hint each attempt got.
- **`wave_size`** (opt-in, default `0`/off): candidates in a cycle normally all fire in parallel with zero visibility into each other, so `already_explored` only ever reflects *prior cycles*. Setting `wave_size` to a positive number (e.g. `5`) stages the cycle into sequential waves of that size, feeding each wave's own core_claims into the next wave's `already_explored` list — genuine cross-candidate awareness within a single cycle. The tradeoff is real: a `--candidates 50` cycle at `wave_size: 5` takes roughly 10x longer wall-clock than fully parallel, for the same dollar cost.

### Avoiding false confidence: the numeric_check

Every candidate can optionally include a `numeric_check`: one concrete, small numeric example the model itself proposes as evidence for a checkable arithmetic claim (e.g. a specific parametric construction). Hanno evaluates it independently and mechanically — using a restricted arithmetic-only expression evaluator, never executing arbitrary model-generated code — and the result feeds back into `path_score`:

- **Fails** → `path_score` is forced to `0.0`, regardless of how confident the model's own `self_rated_rigor` was. This is what would have caught the broken Erdős–Straus formula from the earlier manual review (a plausible-looking construction whose denominator was negative for every case it claimed to cover).
- **Passes** → a small capped bonus to `path_score`.
- **Not provided** (qualitative claims, e.g. framework proposals) → no effect either way.

This is not a proof system — a single passing example doesn't verify a universal claim, and it only catches what the model chooses to test — but it's a real, independent, mechanical check where before there was none. `hanno trace` shows the `numeric_check` result and the witness itself for every attempt.

**Claim types.** Every candidate also declares a `claim_type`, which gates how the witness affects `path_score`:

- `construction` — exhibits explicit objects (a labeling, a solution triple, an instantiated formula). Witness **required**: passing earns the bonus, skipping it costs a ×0.6 penalty. Assignments may contain only primitive inputs; derived quantities (a gcd, an edge label) must be computed inside the equation.
- `asymptotic` — big-O/growth/density/limit claims. One example can't verify these, so the witness is ignored for scoring — a "passed" witness that only re-checks the model's own invented numbers earns nothing (a failure mode observed repeatedly in real runs).
- `structural` — qualitative frameworks/connections. Neutral; no bonus reachable.

A failing witness still zeroes the score for every claim type. The evaluator itself understands `abs`, `min`, `max`, `len`, `sum`, `gcd`, `floor`, `ceil`, set/list literals, and `in` — so honest combinatorial witnesses like "all edge labels distinct" (`len({e1,e2,e3}) == 3`) are directly expressible, still with no path to executing arbitrary code.

**Anti-tunneling in iterative mining.** Real testing showed a `--depth 4` run spending nearly all its budget refining one tangential sub-topic. Three countermeasures:

- **Beam diversity**: beam picks must be pairwise below `beam_diversity_max_similarity` in fingerprint cosine similarity; a too-similar runner-up is skipped (the beam may come back smaller than `--top-k`).
- **The wildcat thread** (`iteration_exploration_thread`, on by default): every iteration ≥ 2 also runs one fresh, unrefined pass at the original seed, competing for beam slots on equal footing — the beam can never fully lock into one sub-topic.
- **Plateau stop** (`iteration_plateau_patience`, off by default): optionally stop early once the best score stops improving, instead of paying for further stuck iterations.

---

## 4. Configuration

The default configuration lives in `config/default.yaml`. Important values:

| Section | Key | Default | Meaning |
|---------|-----|---------|---------|
| `excavator` | `daily_spend_limit_usd` | `25.0` | Hard stop on API spending per day. Only meaningful for `provider: anthropic` — cost estimation has a real per-token pricing table for Anthropic only; `openai_compatible` always reports $0 (covers everything from a free local Ollama server to metered hosted APIs, and Hanno can't know which without guessing), so this limit never triggers on that provider. |
| `excavator` | `provider` | `anthropic` | `anthropic` \| `openai_compatible` — which `ModelClient` backend to use (see `hanno/excavator/client_factory.py`). `openai_compatible` covers OpenAI itself plus any self-hosted or hosted server speaking the same `/v1/chat/completions` wire format (Ollama, LM Studio, vLLM, Groq, ...) — see `LOCAL_MODEL_SETUP.md` for a local Ollama walkthrough. Skills/tool-calling work on both providers via each one's own wire format (Anthropic tool-use blocks vs. OpenAI function-calling) — whether a given `openai_compatible` model actually uses them depends on whether that specific model supports tool-calling; Hanno degrades gracefully either way (see `hanno/excavator/openai_client.py`) rather than crashing a candidate over it. |
| `excavator` | `base_url` | `null` | Only used when `provider: openai_compatible` — e.g. `http://localhost:11434/v1` for a local Ollama server. Required whenever `provider` is `openai_compatible`; `hanno mine` etc. fail with a clear error if it's missing. |
| `excavator` | `model` | `claude-sonnet-4-5-20250929` | Model name, interpreted per-provider (a Claude model id for `anthropic`, the server's own model tag for `openai_compatible`, e.g. `deepseek-r1-14b-hanno`) |
| `excavator` | `prover_provider` / `prover_model` / `prover_base_url` | `null` | Optional per-role model override for the Prover step only (EXCAVATOR_MIGRATION_PLAN.md Phase 6) — e.g. a cheap/fast model for formalization, a stronger dedicated model for the actual proof search. Setting `prover_model` is what activates a separate client; the other two fall back to the main `provider`/`base_url` if unset. Used by `hanno solve`, `hanno benchmark`, and `hanno discoveries verify`. |
| `excavator` | `request_timeout_seconds` | `600.0` | Per-call HTTP read timeout. Hosted APIs finish well inside 600s; self-hosted local models are much slower tok/s and `max_tokens_per_candidate`/`deep_reasoning_max_tokens` can genuinely take longer than that to finish generating — raise this (e.g. `1800`-`3600`) or long local responses get killed mid-generation and logged as a plain failed candidate. See `LOCAL_MODEL_SETUP.md`. |
| `excavator` | `max_tokens_per_candidate` | `2000` | Output budget per candidate |
| `excavator` | `candidates_per_cycle_per_seed` | `50` | Default `--candidates` |
| `excavator` | `dead_end_reseed_threshold` | `0.6` | Fraction of `dead_end`-signaling candidates in a cycle that triggers a reseed recommendation |
| `excavator` | `arm_selection_strategy` | `ucb1` | `ucb1` adaptively favors better-scoring (temperature, variant, reasoning_effort) combos; `round_robin` is the old exhaustive fallback |
| `excavator` | `ucb1_exploration_constant` | `1.4` | Higher = explores unproven combos more; lower = exploits the current best combo harder |
| `excavator` | `reasoning_efforts` | `["standard", "deep"]` | `deep` uses real extended thinking (costs more tokens, forces temperature to 1.0 for that call) |
| `excavator` | `deep_thinking_budget_tokens` | `4096` | Extended-thinking token budget for `deep` arms |
| `excavator` | `deep_reasoning_max_tokens` | `8000` | Response token budget for `deep` arms (must exceed the thinking budget) |
| `excavator` | `max_prior_claims_in_prompt` | `15` | Most-recent prior core_claims for this seed fed back into every prompt, so candidates diverge instead of reconverging on the same angle |
| `excavator` | `wave_size` | `0` | `0` = full parallel (fastest). `>0` stages the cycle into sequential waves of this size, feeding each wave's own core_claims into the next wave's prompts — real intra-cycle diversity, at the cost of wall-clock time |
| `excavator` | `iteration_top_k` | `2` | Beam width for `hanno mine --depth`: how many top candidates carry forward into the next iteration |
| `excavator` | `strong_path_score_threshold` | `0.6` | Best-of-beam `path_score` that must be sustained for a "strong path" reseed recommendation |
| `excavator` | `min_iterations_for_reseed` | `2` | Consecutive iterations that must clear the threshold above before `STRONG PATH FOUND` fires |
| `excavator` | `beam_diversity_max_similarity` | `0.85` | Beam members must be pairwise below this fingerprint cosine similarity (`1.0` disables) — two rewordings of one idea can't hold two beam slots |
| `excavator` | `iteration_exploration_thread` | `true` | Each iteration ≥ 2 also mines the *original* seed fresh (the "wildcat" thread), competing for beam slots — anti-tunneling, ~+1 thread of cost per iteration |
| `excavator` | `iteration_plateau_patience` | `0` | Stop iterating early after this many consecutive iterations without a new best score (`0` disables) |
| `excavator` | `iteration_lineage_retention_threshold` | `0.5` | A lineage whose best-ever `path_score` clears this bar stays eligible for the next beam even if this iteration's refinement of it scored lower (`0` disables) |
| `excavator` | `max_lean_checks_per_iteration` | `3` | `--until-promising`: how many of this iteration's eligible candidates (novel, `path_score` clears `strong_path_score_threshold`) get Lean-checked — not just the single top-scoring one. Self-rated `path_score` alone isn't trustworthy enough to gate the entire check budget down to one winner per iteration; a higher-scoring but wrong candidate must not crowd out a genuinely good one that merely self-rated lower. |
| `fingerprint` | `embedding_model` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model |
| `fingerprint` | `compressed_dimensions` | `128` | Reduced vector size |
| `fingerprint` | `similarity_threshold_near_duplicate` | `0.92` | Cosine similarity flag for near dup |
| `fingerprint` | `similarity_threshold_exact_duplicate` | `0.99` | Cosine similarity flag for exact dup |
| `ledger` | `database_path` | `data/hanno.db` | SQLite ledger location |
| `ledger` | `export_path` | `data/exports/` | Default export directory |

### Environment overrides

Any YAML value can be overridden by an env var. Use double underscores for nesting and UPPERCASE with the `HANNO_` prefix:

```bash
export HANNO_EXCAVATOR__DAILY_SPEND_LIMIT_USD=10.0
export HANNO_FINGERPRINT__SIMILARITY_THRESHOLD_NEAR_DUPLICATE=0.85
export HANNO_LEDGER__DATABASE_PATH="data/my_hanno.db"
```

### Calibration events

The first time a config loads, the system silently snapshots the baseline. After that, every change to any value is recorded as a `calibration_event` in the ledger, so the pipeline is fully reproducible and auditable.

---

## 4b. Skills — giving the model real tools

Skills let the miner hand the model actual instruments — a computer algebra system, a graph checker, any local program — that it can call **during** mining to test its ideas, the way a programmer runs code instead of assuming it works. Each skill is one YAML file in `skills/`:

```yaml
name: graceful_check                      # becomes the Anthropic tool name
description: >                            # what the MODEL reads — tell it when/how to use the tool
  Verify a graceful labeling ... input JSON {"edges": ..., "labels": ...}
command: ["python3", "skills/scripts/graceful_check.py"]
input_mode: stdin                         # or "argument" with an {input} placeholder in command
timeout_seconds: 10
max_output_chars: 4000
enabled: true
```

Drop a file in, it's registered — no code changes. `hanno skills` lists everything (including disabled files). When at least one skill is enabled, `hanno mine` calls become agentic: the model can invoke a tool, Hanno executes the command locally, and the result is fed back into the model's reasoning — up to `skills.max_tool_rounds_per_candidate` (default 3) round-trips per candidate, since each round re-bills the conversation. Every invocation (input, stdout/stderr, exit code, duration) is recorded in the append-only `tool_invocations` table and shown in `hanno trace` (use `--full` for complete tool I/O; the `--json` export includes them too). With no skills registered, behavior is exactly as before.

**The generic tool vs. hand-authored tools.** There are two ways to give the model instruments, and they trade off effort against safety:

- **Hand-authored, safe tools** (like `graceful_check` and `tree_lab`) are scripts *you* write that only parse JSON and compute — they never execute model text as code, so they're safe to leave enabled. The cost is that each only serves one kind of problem; a new domain needs a new script.
- **The generic tool** `run_code` needs no per-problem authoring: the model writes a short Python program to test whatever its current idea needs (check a construction, loop to hunt a counterexample, run a small simulation) and Hanno executes it. One tool, every domain — the model decides the computation. The cost is that this *is* arbitrary model-written code running on your machine.

**Security — read this before enabling `run_code` or `sympy_eval`:** a skill executes a local command with model-supplied input. A skill whose command *interprets that input as code* (`run_code`, `sympy_eval`) is opt-in **arbitrary code execution by the model on your machine**. That's why both ship `enabled: false` — flipping them is your deliberate decision as the miner. `run_code`'s executor applies best-effort containment (CPU/memory/file-size limits, blocked network, throwaway working directory, on top of the timeout, output cap, and round budget every skill gets) — but that is *containment, not a security boundary*: a determined escape is possible. If you enable it, run Hanno itself inside a container or VM. The safe tools (`graceful_check`, `tree_lab`) have no such caveat. Four included examples:

| Skill | What it does | Default |
|---|---|---|
| `graceful_check` | Verifies one graceful labeling of a tree (JSON in, verdict out; never executes model text) | enabled |
| `tree_lab` | Searches for a graceful labeling, generates random trees, checks labelings — safe search/generation instruments | enabled |
| `mathlib_search` | Read-only, no code execution: searches the real vendored Mathlib + Lean core source for a lemma/theorem OR a tactic by exact name (`EXISTS` + real signature/declaration, tagged `[lemma/theorem]` or `[tactic]`, or `NOT FOUND` + closest real matches) or by keyword. Grounds formalize/prove against real Mathlib instead of a hallucinated name — see `hanno solve`/`hanno discoveries verify` in Section 5. | enabled |
| `lean_goal_solver` | Executes the real Lean compiler (short, disposable scratch files only) against a battery of standard closing tactics (`omega`, `decide`, `norm_num`, `simp`, `positivity`, `exact?`) for a given standalone sub-goal, and reports which one (if any) actually works — verified, not guessed. The generic complement to `mathlib_search`: that tool grounds *name* lookup, this one grounds *tactic selection* (e.g. catches committing `omega`, a linear-arithmetic-only tactic, to a goal involving exponentiation, which it can never close regardless of the fact being true). | enabled |
| `run_code` | **Generic:** runs arbitrary model-written Python to test any idea — best-effort sandbox | **disabled** |
| `sympy_eval` | Runs model-written SymPy scripts — the "Maple-style" CAS lane (`pip install sympy` first) | **disabled** |

| Config key | Default | Meaning |
|---|---|---|
| `skills.enabled` | `true` | Master switch for the whole mechanism |
| `skills.directory` | `skills` | Where the YAML definitions live |
| `skills.max_tool_rounds_per_candidate` | `3` | Model→tool→model round-trips allowed per candidate |

---

## 5. CLI reference

### Top-level

```bash
hanno [COMMAND] --help
```

### `hanno seeds add TEXT --domain DOMAIN [--submitter ID] [--type TYPE]`

Submit a new seed.

- `TEXT` — positional argument, 10–200 words.
- `--domain` — required. One of `mathematics`, `science`, `philosophy`, `cross-domain`.
- `--submitter` — optional human identifier.
- `--type` — optional, default `human`. One of `human`, `ai-generated`, `derivative`. Use `ai-generated` when submitting text copied from a `hanno mine` "SUGGESTED FOLLOW-UP SEEDS" recommendation, so its provenance stays traceable in `hanno seeds list` and `hanno trace`.

### `hanno seeds list [--domain DOMAIN]`

List seeds ordered by the priority queue.

### `hanno mine --seed SEED_ID [--candidates N] [--depth D] [--top-k K] [--fresh] [--until-promising]`

Run an excavation cycle against a single seed. Requires `ANTHROPIC_API_KEY`.

- `--seed` — required seed UUID.
- `--candidates` — number of candidates to request per iteration (default `50`).
- `--depth` — iterative mining depth (default `1`, unchanged single-cycle behavior). `>1` turns mining into a best-first beam search: each iteration refines the most promising candidate(s) from the previous one instead of re-exploring the seed from scratch.
- `--top-k` — beam width for `--depth > 1` (default from `excavator.iteration_top_k`, currently `2`): how many top candidates carry forward into the next iteration.
- `--fresh` — for `--depth > 1`: restart the beam search from the original seed at iteration 1 instead of resuming (see below).
- `--until-promising` — keep iterating until a candidate independently clears the Lean triviality filter, or `--depth` is reached, whichever comes first (see below). Requires the local Lean toolchain.

**Cumulative iterations across runs.** With `--depth > 1`, re-mining an already-mined seed *continues* the previous search by default rather than restarting it: the beam is rebuilt from the most recent iteration in the ledger that still has eligible candidates, and iteration numbering carries on from where it stopped. Mining a seed at `--depth 3` and later again at `--depth 2` yields one cumulative 5-iteration lineage (iterations 4–5 refine the best candidates of iteration 3). The plateau and `STRONG PATH FOUND` logic also see the resumed history, so sustained progress can span runs. If the last iteration holds only dead ends or parse failures, resume walks back to the last usable iteration; if nothing in the seed's history is eligible, mining falls back to the original seed text but numbering still continues. Pass `--fresh` to opt out and restart at iteration 1.

**Iterative mining (`--depth > 1`)**

Instead of independently re-sampling the same broad seed text every cycle, each iteration narrows in: it takes the best candidate(s) from the previous iteration (ranked by `path_score`, excluding exact duplicates, dead ends, and anything whose `numeric_check` mechanically failed) and builds a refined seed text asking the model to deepen, formalize, or resolve the weaknesses in *that specific idea* — not propose something unrelated. With `--top-k` > 1 this is a real beam search: every surviving thread gets independently expanded into its own cycle each iteration, and the *global* top-K candidates across all threads (not top-1-per-thread) form the next beam, so a weak thread can be dropped entirely and a strong one can spawn multiple survivors.

**Cost warning:** cost multiplies roughly by `--top-k` for every iteration beyond the first (iteration 1 uses 1 thread, iterations 2+ use up to `--top-k` threads, each generating a full `--candidates` batch). `--depth 3 --top-k 2 --candidates 10` can cost on the order of `10 + 2×10 + 2×10 = 50` candidates' worth, not `30`.

**Promising-lineage retention.** A lineage whose best-ever `path_score` clears `excavator.iteration_lineage_retention_threshold` (default `0.5`) is never dropped from the beam just because *this* iteration's refinement attempt at it underperformed — its own current-best candidate stays in the pool as a fallback, competing purely on score against fresh children and the wildcat thread. A genuinely better new idea still wins the slot fairly; retention only stops a proven idea from being replaced by a worse one. The lineage keeps getting fresh refinement attempts each iteration (still costs the same as normal) — it just doesn't lose its place when one attempt has a bad roll. Set the threshold to `0` to restore the old behavior (only this iteration's fresh children ever compete). Real testing showed the old behavior discarding a 0.66-scoring algorithmic idea because its one refinement attempt scored 0.40, in favor of an unrelated fresh idea — losing the thread entirely rather than giving it another shot.

**Human reseed is gated much more strictly here than the per-cycle `RESEED RECOMMENDED` message.** A single cycle's `branch_point` signal can fire on routine output — real testing showed it firing on an ordinary cycle. Iterative mode requires the **same refinement lineage** — not just the best score anywhere in the beam that iteration — to stay at or above `excavator.strong_path_score_threshold` (default `0.6`) for `excavator.min_iterations_for_reseed` (default `2`) *consecutive, freshly-generated* iterations before printing `STRONG PATH FOUND`. A retained (not freshly refined) iteration resets that streak to 0 for the purposes of this check — sitting at a good score because nothing beat it isn't the same as independently reconfirming it — though the lineage itself is unaffected and keeps trying. This also means the lineage reported in `STRONG PATH FOUND` (and used by `hanno trace --lineage`) is whichever thread actually earned the sustained streak, which is not always the single highest-scoring candidate in the final beam.

Use `hanno trace SEED_ID --lineage CANDIDATE_ID` (with the final candidate ID from a `STRONG PATH FOUND` message) to see the full refinement chain: the original seed's candidate, then each iteration's winning refinement, in order.

**`--until-promising`: stop as soon as the Lean filter can't dismiss something.** With this flag, `--depth` stops being a fixed target and becomes a safety ceiling instead — mining keeps going (subject to the usual plateau / no-eligible-candidate stop conditions) until either a promising candidate is found or `--depth` iterations have run. After each iteration, up to `excavator.max_lean_checks_per_iteration` (default `3`) of this iteration's eligible candidates get checked — not just the single top-scoring one. "Eligible" means novel (not a near/exact duplicate) and `path_score` already clears `excavator.strong_path_score_threshold`, ranked by score but no longer limited to one: self-rated `path_score` isn't trustworthy enough on its own to let a single high (and possibly wrong) self-rating consume the entire iteration's check budget and starve a genuinely good candidate that happened to self-rate lower. The check itself is the same translate-then-verify pipeline as `hanno lean-pilot`: translate the informal claim into real Lean 4 (retrying with the actual compiler error fed back, up to `lean_verifier.max_translation_attempts`), then see if `exact?`/`simp` can close it trivially. The first genuine survivor — compiles, and neither tactic closes it — stops mining immediately, prints `PROMISING CANDIDATE FOUND`, and is recorded as a real row in the `discoveries` table (`lean_verified=1`, `human_reviewed=0`) — a concrete, human-reviewable flag, not just a print statement that scrolls away; the rest of that iteration's eligible candidates are skipped once a survivor is found. Every check, found or not, still writes a `lean_verifications` row either way, same as the batch pilot. A candidate already checked (e.g. kept alive by lineage retention) is never re-checked. Requires the local Lean toolchain set up first (see `hanno lean-pilot` above) — `hanno mine` fails fast with a clear error if `lean_verifier.mathlib_cache_path` doesn't exist, rather than burning mining cost on a run that could never actually check anything.

### `hanno stats`

Show cycle totals, parse success rate, today's spend, and cumulative token usage (input/output tokens across all cycles, plus average tokens per candidate). Token counts are estimates-grade (from the Anthropic API's own `usage` field) but the dollar cost is still a hardcoded-price estimate, not billing truth.

### `hanno export [--path PATH]`

Dump the ledger as JSON. Defaults to `data/exports/hanno_export_YYYYMMDDTHHMMSSZ.json`.

### `hanno trace SEED_ID [--full] [--limit N] [--lineage CANDIDATE_ID] [--json PATH]`

Print the full mining trace for one seed, in order: seed metadata (type — `human`/`ai-generated`/`derivative` — domain, status, submitter), then every mining attempt against it, each showing the *exact prompt sent* and the *raw model response*, plus its variant, temperature, iteration, parse/duplicate/exploration status, and token counts.

- `--full` — show untruncated prompt/response text (default: clipped to 400 chars each).
- `--limit N` — show only the first N attempts.
- `--lineage CANDIDATE_ID` — show only that candidate's refinement chain (walks `refined_from_candidate_id` back to iteration 1), instead of every attempt for the seed. Use this with the candidate ID from a `STRONG PATH FOUND` message.
- `--json PATH` — write the trace as JSON instead of printing it, for the mind-map viewer below.

This is the review surface for "how is the Excavator actually trying to find a discovery" — nothing is summarized away; it reads straight from the append-only ledger.

### Mind-map viewer (`hanno/viz/trace_mindmap.html`)

A self-contained, offline HTML/JS page (no build step, no server, no external dependencies) that turns a trace into a radial constellation chart: the seed at the center, every candidate positioned by iteration and connected along its actual refinement lineage — the same shape as a beam-search run.

```bash
hanno trace <SEED_ID> --json trace.json
open hanno/viz/trace_mindmap.html   # or just double-click it
```

Then drag `trace.json` onto the page, or use "Load trace JSON." Node size and color intensity track `path_score`; border style marks duplicate status (solid = novel, dashed gold = near-duplicate, dashed rust = exact duplicate); a gold star marks `branch_point`, a green check or red cross marks a passed/failed `numeric_check`, and a blue gear below the node (with a count if more than one) marks a candidate that called a skill mid-generation — click it open for the full breakdown: each skill name, exit code, duration, the exact input sent, and its stdout/stderr. Click any node for the full detail panel (prompt, formal statement, numeric_check equation, connections, skills used) — click a lineage button to highlight the whole refinement chain back to the root. The left rail has live stats, a core-claim search, score/status filters, and a legend. Nothing here is uploaded anywhere — it's a local file reading a local JSON file via the browser's file picker.

### `hanno ui [--host 127.0.0.1] [--port 8420]`

Starts a local web server (FastAPI + uvicorn, localhost-only by default) with a UI for configuring the miner and watching a seed mine live:

```bash
hanno ui
# then open http://127.0.0.1:8420
```

Four tabs, all sharing the same navy/gold design as the mind-map viewer above:

- **Mine** — pick a seed (dropdown or paste an ID), set candidates/depth/top-k, and click **Start Mining**. This *is* the mind-map viewer from above, just fed live: it polls the seed's trace every ~1.5s while a job is running, so new candidates appear as nodes the moment they're recorded in the ledger — no separate "live view," the same component handles both. "Load trace JSON" still works here too, for reviewing an exported trace from a past run. Every candidate node also has a small gold **+** badge (bottom-right corner) — click it (or the "Reseed from this candidate" button inside the detail panel) to open a **Reseed** modal dedicated to just this action, kept separate from the dense candidate detail view: a boxed "Context — what this reseed builds on" section at the top shows the source candidate's claim type, iteration, domain, duplicate status, path score, and its full formal statement, so you know exactly what you're building on; below that, a clearly separate "Your new seed" section is where the human input goes — pick one of the candidate's `suggested_seeds` (one click fills it in) or write your own, choose a domain, and click **Submit reseed**. Close with the **×** button, the Escape key, or a click outside the modal. This is human-in-the-loop steering — you decide which candidate is worth following up, not just the miner's own `suggested_seeds` field sitting unused in a trace file. Every reseed is recorded in the append-only `reseed_events` table (`original_seed_id`, `parent_seed_id`, `generated_reseed_id`, `triggering_candidates`, `chain_depth`) so a multi-hop chain of reseeds always resolves back to its true root seed, even after several rounds — reseeding a candidate that itself came from an earlier reseed just increments `chain_depth` rather than starting a new chain.
- **Seeds** — submit a new seed (text, domain, type, optional submitter) with the same validation `hanno seeds add` runs (length bounds, domain-keyword presence, concept-level duplicate check — flagged, never auto-rejected), and browse the priority-ordered queue (same ranking as `hanno seeds list` / `SeedManager.next_for_domain`), filterable by domain. A seed currently being mined (by this browser or any other) gets an unmissable gold-glow border, a pulsing "MINING NOW" badge, and its **Mine this seed** button is disabled (mining twice concurrently is rejected server-side anyway) in favor of a **View live trace →** button that jumps to the Mine tab and keeps polling. Any seed that already has cycles/candidates gets a **View trace →** button even when it's not currently mining, for reviewing past runs without starting a new one. The queue auto-refreshes every 3s while this tab is open, so another session's mining run shows up here without a manual reload.
- **Settings** — provider, base URL, model, request timeout, daily spend limit, candidates per cycle, temperatures, arm selection strategy, iteration/beam knobs (top-k, strong-path threshold, lineage retention threshold, plateau patience, wildcat thread), and an API key. Miner settings save to `config/local.yaml` (gitignored) as an overlay on top of the checked-in `config/default.yaml` — the documented defaults are never rewritten, and an invalid value is rejected before anything touches disk. Switching **Provider** to `openai_compatible` reveals the **Base URL** field (e.g. `http://localhost:11434/v1` for a local Ollama server per `LOCAL_MODEL_SETUP.md`) — required whenever that provider is selected. The API key is saved **per provider** (`data/secrets.local.yaml`, gitignored, `chmod 600`) — switching providers and back doesn't lose a previously saved key, and self-hosted servers that don't check auth can simply be left without one. The form never shows a full key back, only "ends in XXXX" after saving.
- **Skills** — the same skills `hanno skills` lists, with an enable/disable toggle per skill. Toggling flips only the `enabled:` line in that skill's YAML file via a targeted text edit — every comment (including security-rationale headers like the one in `skills/run_code.yaml`) survives untouched. No command/timeout editing here; author those by hand in the YAML file directly.

Seeds submitted through the UI are permanent, like every other ledger row — the append-only design (Section 6) means there's no delete. Double-check text/domain before clicking **Add seed**.

A mining job started from the UI runs in-process as a background task — starting the same seed twice concurrently is rejected (409). Restarting `hanno ui` forgets in-flight job status, but every candidate a job already produced stays in the ledger regardless, same as if you'd killed a `hanno mine` process.

Every tab and every seed viewed in the Mine tab is a real URL (`#mine`, `#mine/<seed_id>`, `#seeds`, `#settings`, `#skills`), so the browser's **back/forward buttons** work as expected — switching tabs, picking a different seed, or jumping to a seed from the Seeds tab's "View trace"/"Mine this seed" buttons are all real, navigable history entries. Reloading the page returns to whatever tab/seed the URL points at.

### `hanno lean-pilot [--limit N]`

A validation pilot for the Lean-based triviality filter (Step 6, previously stubbed and unused since Step 1 — see `LeanVerifierConfig` in `config/default.yaml`). For up to `N` (default 50) already-mined `mathematics` candidates that haven't been checked yet, this:

1. Translates the candidate's informal `formal_statement` into an actual Lean 4 `theorem ... := by sorry` statement via a single one-shot Claude call (not the full agentic Excavator loop — just a translation).
2. Typechecks it locally against Mathlib (`lake env lean`).
3. If it typechecks, tries closing the `sorry` with `exact?` then `simp` — if either succeeds, the claim was trivially known already.

Every attempt writes a real row to the (previously empty) `lean_verifications` table: `UNTRANSLATABLE` (couldn't be expressed in Lean), `MALFORMED` (didn't typecheck), `KNOWN` (typechecked but closed trivially), `TIMEOUT`, or unset/`None` for a genuine **survivor** — compiles, and neither `exact?` nor `simp` closes it. Survivors are printed at the end for a quick look; this pilot never claims `VERIFIED` itself — that's what `hanno discoveries verify` (below) is for, since actually proving something needs a real proof-search attempt, not a translation pass.

Requires a one-time local Lean toolchain setup (not run automatically): `elan` (Lean's version manager), a Lake project at `data/mathlib_cache` (the exact path `LeanVerifierConfig.mathlib_cache_path` already names) depending on Mathlib, and `lake exe cache get` to pull precompiled Mathlib rather than building it from source. No AWS, no self-hosted inference, no new mining — this reuses candidates already sitting in the ledger from real runs.

### `hanno report`

**Stub.** Returns a message that full reports arrive in Step 7.

### `hanno discoveries list`

Lists every row in the (previously unused) `discoveries` table — one per candidate that `hanno mine --until-promising` found and stopped on — with a derived status per row: `not yet verified`, `REFUTED (counterexample found)`, `PROVEN (complete Lean proof found)`, or `unresolved (no counterexample, but no automated proof either)`. The status is computed by looking at that candidate's `tool_invocations` and `lean_verifications` rows, not stored directly — the append-only ledger has no update, so "verifying" a discovery never rewrites its original row, it just adds more rows elsewhere that `list` reads back.

### `hanno discoveries verify [--limit N]`

Deep verification for discoveries that haven't been checked yet (default `N=10`) — the step between "the miner stopped here" and "a human looks at it." Two stages, in cost order, each gating the next:

1. **Counterexample search.** A one-shot Claude call writes a Python program that stress-tests the claim across many concrete cases (hundreds+, including edge cases), run through the same sandboxed `run_code` skill executor used during mining (CPU/memory/output limits, no network, 25s timeout — see `skills/run_code.yaml`) regardless of whether that skill is currently toggled on for live mining. A real counterexample is a definitive, immediate rejection — recorded as a `tool_invocations` row, and proof search is skipped entirely for that candidate.
2. **Real proof search** (only for survivors of stage 1). Unlike `hanno lean-pilot`'s one-shot `:= by sorry` translation, this is an *interactive* loop: submit a proof attempt, get Lean's actual error back (or, if it compiles but still contains `sorry`, a note that the proof is incomplete), retry — up to `lean_verifier.max_proof_attempts` (default `5`, a separate and larger budget than `max_translation_attempts` since proving is a harder ask than translating). A complete, `sorry`-free proof that compiles writes a second `lean_verifications` row for that candidate with `result="VERIFIED"` — the first time this codebase ever emits that value, since it requires an actual proof, not just a non-trivial statement. Exhausting the attempt budget without one is recorded honestly (`result` left unset) — most hard, genuinely novel conjectures are expected to land here, not get a machine proof; that's not a failure of the pipeline, it's the actual difficulty of the problem.

Same pre-flight checks as `hanno mine --until-promising`: fails fast if the local Lean toolchain isn't set up, and if `skills/run_code.yaml` can't be found. Costs real API tokens (one call per stage per candidate, plus retries) and real wall-clock time (each Lean invocation is ~45-60s, and the proof-search loop can run several) — use `--limit` deliberately.

Deliberately not automated further: whether the Lean formalization faithfully captures the original English claim, and whether the result is actually significant, stay human judgment calls regardless of a `PROVEN` verdict here — this step narrows what a human needs to look at, it doesn't replace them.

### `hanno solve SEED_ID --claim "..."`

SOLVE mode (EXCAVATOR_MIGRATION_PLAN.md Phase 1) — the direct fix for a real failure mode where the Excavator's normal "propose something non-obvious" instruction let a model drift away from a simple, concrete claim into fabricated abstractions instead of just checking it. `hanno solve` skips candidate generation entirely: given one fixed claim and an existing seed to attach it to, it runs formalize → (optional counterexample search, if the `run_code` skill is enabled) → real proof search, and nothing else. There is no "be novel" framing anywhere in this path, and no self-rated scoring.

The result is written as a real `Candidate` row (`prompt_variant="solve_mode_direct"`, `temperature=0.0`, the claim as both `core_claim` and `formal_statement`) — zero ledger schema changes, so `hanno trace`, `hanno discoveries list`, and the mindmap UI render a SOLVE-mode result exactly like an EXPLORE-mode one. Output is one of: untranslatable/malformed/timeout (red), trivial — closed by `exact?`/`simp` before a real proof was even attempted (yellow), `REFUTED` with the counterexample (red), a green `SOLVED` banner with the complete proof, or an honest unresolved message pointing at `hanno trace` for details. Same pre-flight checks as `hanno lean-pilot`: fails fast if the local Lean toolchain isn't set up.

Both formalize and prove offer `mathlib_search` and `lean_goal_solver` as tools if enabled (name grounding and tactic-selection grounding, respectively — see Section 4b), and prove tries decomposition first (EXCAVATOR_MIGRATION_PLAN.md Phase 6): if an attempt already typechecks as a `have h1 : ... := by sorry, have h2 : ..., ...` sketch, each stub is solved independently before falling back to regenerating the whole proof. If `excavator.prover_model` is set, proving uses that separate (e.g. stronger/dedicated) client while formalize/counterexample keep using the main model.

### `hanno benchmark`

Runs the fixed 5-case solvable-claim benchmark (`benchmarks/solve_benchmark.yaml`) through SOLVE mode and reports a solve-rate and real `$` cost — the regression number Phase 0 introduced and every later migration-plan phase is judged against. Deliberately narrow, concrete claims only (e.g. `n^3 - n` divisible by 6) — the fixture's own header warns against adding "this generalizes to..." framing, since that framing is the diagnosed cause of the original goal-drift disaster. Each run creates fresh `Seed` rows (`submitter_id="benchmark"`), so it's safe to re-run repeatedly, e.g. right before and after a config or prompt change, to see whether it helped or hurt.

---

## 6. How seed/candidate uniqueness works

The `FingerprintEngine` turns a seed or candidate into a **semantic fingerprint**:

1. **Normalize** the text.
2. **Embed** it locally with `sentence-transformers/all-MiniLM-L6-v2`.
3. **Reduce** the vector to 128 dimensions (raw truncation until 500 fingerprints, then PCA).
4. **Hash** the compressed vector with SHA-256.
5. **Compare** the new fingerprint against all existing fingerprints of the same entity pool (seeds vs candidates).
6. **Classify**:
   - `NOVEL` — no close match.
   - `NEAR_DUPLICATE` — cosine similarity ≥ near-duplicate threshold.
   - `EXACT_DUPLICATE` — cosine similarity ≥ exact-duplicate threshold, or the SHA-256 hash collides.

This is concept-level deduplication: `“Every finite simple group embeds in S_n with bounded degree”` and `“Can every finite simple group be represented in a bounded-degree symmetric group?”` will be flagged as near-duplicates even though the wording differs.

Important notes:

- Duplicate candidates are still stored but flagged `duplicate_status='exact_duplicate'`/`'near_duplicate'`.
- Seeds are compared against all prior seeds, across all domains, because a cross-domain idea may overlap with a math one.
- The `fingerprint` thresholds are **calibratable** — they are config values, not hardcoded.

---

## 7. The mining workflow

When you run `hanno mine --seed <ID>`:

1. The seed is loaded and validated.
2. The Excavator builds prompts (universal + math-specific variants, multiple temperatures). Every prompt asks the model to rigorously self-check novelty against known results, and to judge the seed direction itself: `continue` (default), `branch_point` (this idea opened up multiple distinct fruitful sub-directions), or `dead_end` (this direction looks exhausted).
3. Claude is called asynchronously with a daily spend cap enforced. Input/output token counts from each call are recorded.
4. Each response is parsed defensively for JSON candidate lists.
5. Every parsed candidate is fingerprinted and classified.
6. Candidates are appended to the ledger with `duplicate_status`, `exploration_signal`, `suggested_seeds`, and token counts.
7. The seed's `cycles_run` and `candidates_produced` counters are updated.
8. A cycle summary is printed (including tokens used) and a `cycle_event` is recorded with token totals.
9. If candidates signaled `branch_point` or a majority signaled `dead_end`, a reseed recommendation and any suggested follow-up seed text are printed — the human decides whether to act on it.

If the spend limit is reached mid-cycle, the command halts and prints a warning, but everything generated so far is preserved.

**Design intent:** the Excavator explores a single seed in every logical direction it can find on its own, and only asks the human for a new seed when a candidate concretely signals that the current direction has branched or gone dry — not on every cycle.

---

## 8. Ledger and data files

The MVP creates these local files automatically:

- `data/hanno.db` — SQLite append-only ledger.
- `logs/hanno.log` — rotating JSON logs.
- `data/exports/` — output directory for `hanno export`.
- `.pytest_cache/` and `data/` may also appear during use.

**Append-only means:** rows are never `UPDATE`d or `DELETE`d. Status and counters are derived by replaying events. The database even has triggers that forbid `UPDATE`/`DELETE` on the ledger tables.

---

## 9. Running the test suite

```bash
.venv/bin/pytest -q
```

Expected:

```
54 passed
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ANTHROPIC_API_KEY not set` / API errors | Missing API key | `export ANTHROPIC_API_KEY=...` |
| `ERROR: only the mathematics domain is implemented` | Tried to mine science/philosophy | Use `--domain mathematics` |
| `HALTED: daily spend limit reached mid-cycle` | Spend limit hit | Wait until tomorrow or raise `excavator.daily_spend_limit_usd` |
| `candidates failed` > 0 | Model returned unparseable JSON | Normal; failed responses are logged but not counted as candidates |
| `seeds add` rejected | Too short/long or missing domain keyword | Check word count and that the text contains math/science/philosophy terminology |
| `sqlite3` or model download error on first run | Missing `sentence-transformers` cache | First embedding download may take a minute and requires internet |

---

## 11. Limitations and next steps

**Current limitations:**

- Only the **mathematics** domain has an excavator pipeline.
- A real Lean verifier now exists (`hanno lean-pilot`, `hanno mine --until-promising`, `hanno discoveries verify`) — triviality filtering, counterexample search, and real proof-search attempts — but no literature/arXiv cross-check and no independent self-critique pass yet; both are natural next steps, not built.
- `hanno report` is still a stub. `hanno discoveries` is real (`list`, `verify`) but only for whatever `--until-promising` has flagged so far.
- The initial 10-seed math corpus from the strategy spec is not bundled; you must add seeds manually.

**Recommended next build steps:**

1. **Step 5 — Sieve**: add AI scoring of coherence, novelty, significance, fertility, formalizability.
2. **Step 6 — Lean 4 Verifier**: formalize math candidates and mint real discoveries.
3. **Step 7 — Reporting**: implement `hanno report`, human review loop, and monthly summaries.

---

## 12. Useful commands cheat sheet

```bash
# Help
hanno --help
hanno seeds add --help
hanno mine --help

# Full workflow
export ANTHROPIC_API_KEY="sk-ant-..."
hanno seeds add "Your concept here" --domain mathematics --submitter you
hanno seeds list
SEED=$(hanno seeds list --domain mathematics | head -1 | awk '{print $2}')
hanno mine --seed "$SEED" --candidates 50
hanno stats
hanno export

# Run tests
.venv/bin/pytest -q

# Web UI: miner/skill settings + live mining view
hanno ui   # then open http://127.0.0.1:8420
```
