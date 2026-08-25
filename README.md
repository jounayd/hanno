# Hanno

**An autonomous mathematical knowledge-mining pipeline.**

Give Hanno a seed idea — a direction to explore, or a concrete claim — and
it uses an LLM plus [Lean 4](https://leanprover.github.io/) / Mathlib to
generate candidate mathematical claims, formalize them, verify or refute
them, and surface genuinely proved, checkable results for human review.
Every step is recorded in an append-only ledger, so the whole process is
auditable and reproducible.

```
hanno seeds add "..." --domain mathematics
hanno mine --seed <ID> --until-promising
hanno discoveries verify
```

---

## Why

LLMs are good at generating plausible-sounding mathematical claims and bad
at knowing which ones are actually true. Hanno's answer is not to trust the
model's self-assessment — it's to make every claim earn its status through
external, checkable signals: a real Lean compile, a real counterexample
search, a real proof search. The model proposes; Lean and Python dispose.

## How it works

Hanno has two operating modes, because "generate diverse ideas" and "prove
this one exact thing" are different jobs with different failure modes:

- **EXPLORE** (`hanno mine`) — generates many candidate claims from a seed
  direction, self-rates and deduplicates them (via a local semantic
  fingerprint engine, not string matching), and pushes survivors through a
  Lean triviality filter.
- **SOLVE** (`hanno solve`) — takes exactly one fixed claim and runs it
  straight through formalize → counterexample search → proof search, no
  generation or exploration involved. This is the direct fix for
  goal-drift: no "be novel" instruction to reward wandering off the actual
  question.

```
 hanno seeds add                  hanno mine                    hanno discoveries verify
 ┌───────────┐   submit    ┌──────────────────┐  survives   ┌──────────────────────┐
 │   Seed    │ ──────────► │ ExcavatorPipeline │ triviality  │ Discovery (pending)  │
 │ (queued)  │             │  (EXPLORE mode)   │ ──────────► │                      │
 └───────────┘             └──────────────────┘   filter    └──────────────────────┘
       │                          │                                    │
       │ FingerprintEngine        │ FingerprintEngine                  │ counterexample
       │ (dedup seeds)            │ (dedup candidates)                 │ search, then
       ▼                          ▼                                    │ real proof search
 same-concept seeds        candidates persisted                        ▼
 flagged, never rejected   with duplicate_status,             VERIFIED / REFUTED /
                           path_score, exploration_signal        unresolved
```

Full architecture, module-by-module breakdown, and the CLI reference live in
[`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md).

## Quick start

Requires Python 3.11+ and, for verification, a local [Lean 4 + Mathlib
toolchain](https://leanprover-community.github.io/get_started.html).

```bash
git clone git@github.com:jounayd/hanno.git
cd hanno
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

export ANTHROPIC_API_KEY="sk-ant-..."   # or use provider: openai_compatible for a local model

hanno seeds add "Explore whether every finite simple group embeds into a bounded-degree symmetric group." --domain mathematics
hanno seeds list
hanno mine --seed <SEED_ID> --until-promising
hanno discoveries list
```

Full step-by-step instructions (installation, configuration, every CLI
command, troubleshooting) are in [`MVP_USER_MANUAL.md`](MVP_USER_MANUAL.md).

Running against a self-hosted local model instead of the Anthropic API:
see [`LOCAL_MODEL_SETUP.md`](LOCAL_MODEL_SETUP.md).

## Project layout

```
hanno/
├── ledger/        append-only SQLite store: seeds, candidates, verifications, discoveries
├── config.py      YAML config + env overrides, with full change-history logging
├── seeds/         seed validation, priority queue, lifecycle
├── fingerprint/   concept-level semantic dedup (embeddings + PCA + cosine similarity)
├── excavator/     candidate generation pipeline (EXPLORE mode)
├── solve/         single-claim prove/disprove pipeline (SOLVE mode) + benchmark runner
├── lean/          autoformalization, triviality checker, proof search
├── verification/  numeric counterexample search
├── skills/        sandboxed tools the model can call during mining (run code, search Mathlib, ...)
├── ui/            local web UI for mining control and live trace inspection
└── cli/           `hanno` command-line entrypoint
```

## Documentation

| Doc | Contents |
|---|---|
| [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) | Full architecture, data flow, module map, CLI reference |
| [`MVP_USER_MANUAL.md`](MVP_USER_MANUAL.md) | Installation, configuration, command-by-command usage, troubleshooting |
| [`EXCAVATOR_MIGRATION_PLAN.md`](EXCAVATOR_MIGRATION_PLAN.md) | The research-grounded redesign plan for the Excavator (EXPLORE mode) |
| [`EXCAVATOR_STRATEGY_AND_CAPACITY_ANALYSIS.md`](EXCAVATOR_STRATEGY_AND_CAPACITY_ANALYSIS.md) | Deeper capacity/strategy analysis |
| [`LOCAL_MODEL_SETUP.md`](LOCAL_MODEL_SETUP.md) | Running Hanno against a self-hosted local model |

## Status

Mathematics domain only. Seed management, the fingerprint engine, EXPLORE
and SOLVE mining, the Lean triviality filter, counterexample search, and
proof search are implemented and tested. See `PROJECT_OVERVIEW.md` §6 for
exactly which parts of the Excavator redesign are shipped vs. pending, and
§7 for what the system explicitly does not automate (literature novelty,
full faithfulness guarantees).

## Testing

```bash
pytest -q
```

## License

TBD.
