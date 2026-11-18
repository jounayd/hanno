# Working on Hanno

Hanno explores mathematical claims and records every outcome in an append-only
SQLite ledger. Read [README.md](README.md), then [docs/development.md](docs/development.md)
for setup and checks. [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) maps the modules.

## Setup and checks

- Use `uv sync --locked`. `.python-version` selects Python 3.12; the package supports Python 3.11+.
- Run commands with `uv run`. `uv run poe check` runs formatting, Ruff, Pyright, and tests.
- Keep repeatable maintenance/check commands in `[tool.poe.tasks]` and `[tool.poe.groups]`.
  Use lowercase `family:variant` names for related tasks (e.g. `test:unit`, `deps:update`),
  short familiar names for primary actions, descriptive help, and logical help groups.
  Document commands in `docs/development.md`; reuse the same names when CI is added.
  `uv run poe --help` lists available tasks. Poe is the project's single task runner.
- `uv run poe lean:setup` prepares pinned Lean/Mathlib; `uv run poe test:lean` requires it.
  Use `uv run poe check:full` for the complete gate with Lean available.
- `uv run poe test:unit` needs neither model downloads nor Lean. Run the affected integration
  tests when changing embeddings or Lean, and state which integrations were unavailable.
- Keep `pyproject.toml` and `uv.lock` together when changing dependencies.
- Do not disable checks or add broad type/lint suppressions to get a passing gate.

## Changes

- Work on a `codex/` branch. Do not commit, push, publish, or open a PR unless requested.
- Preserve user changes. Keep fixes at the layer that owns the behavior, with focused regression tests.
- Treat model responses and generated code as untrusted input. A compiling file alone is not a proof:
  retain the exact target and reject admitted proofs and unexpected axioms.
- Keep the ledger append-only. Record new events rather than editing historical records.
  Schema additions must also migrate existing databases.
- Do not make paid model calls for tests. Use deterministic provider fixtures by default.
- Keep API keys out of source, logs, exports, and test fixtures.
- Skill commands execute code locally. Do not expand their permissions or enable extra tools implicitly.
- Update user-facing documentation when commands, configuration, packaging, or behavior change.
  Detailed documentation belongs in `docs/`; keep the root README short.

## Layout

- `hanno/cli/`: Typer commands; `hanno/ui/`: FastAPI and static browser UI.
- `hanno/excavator/`, `hanno/solve/`, `hanno/lean/`: generation, fixed-claim solving, verification.
- `hanno/ledger/`, `hanno/fingerprint/`, `hanno/seeds/`: persistence, deduplication, seed lifecycle.
- `config/`, `skills/`, `benchmarks/`: editable repository defaults and tools.
- `tests/`: unit tests and explicitly marked integration tests.
- `docs/`: guides, specifications, design history, and development instructions.

Before handing work back, report what changed, checks run, skipped integrations,
and any remaining review findings. Keep the working tree free of generated artifacts.
