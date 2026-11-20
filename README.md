# Hanno

An autonomous mathematical knowledge-mining pipeline. Give Hanno a seed idea or
an exact claim; it uses an LLM, local computational tools, and Lean 4 / Mathlib
to propose, formalize, and check candidates. Every outcome is recorded in an
append-only SQLite ledger for inspection.

Hanno supports mathematics. **EXPLORE** (`hanno mine`) generates and deduplicates
candidate claims; **SOLVE** (`hanno solve`) works on one fixed claim. Automated
checks do not establish literature novelty or replace human review of the
informal-to-formal translation.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone git@github.com:jounayd/hanno.git
cd hanno
uv sync --locked
uv run hanno --help
uv run hanno ui
```

Open [the local UI](http://127.0.0.1:8420) to add seeds and configure a provider.
The default provider uses `ANTHROPIC_API_KEY`; an OpenAI-compatible local server
can run without a key. Embeddings run locally and download their model on first use.

```sh
export ANTHROPIC_API_KEY="your-key"
uv run hanno seeds add \
  "Explore whether every finite simple group embeds into a bounded-degree symmetric group." \
  --domain mathematics
uv run hanno seeds list
uv run hanno mine --seed <SEED_ID> --candidates 5
```

Lean verification (`solve`, `discoveries verify`, and `mine --until-promising`)
also requires a local Lean 4 + Mathlib project. See the
[user manual](docs/MVP_USER_MANUAL.md) for setup and command details. Shipped
execution skills run model-written code locally; their containment is not a
security boundary. Read the skill definitions before running mining jobs.

## Development

```sh
uv run poe check       # formatting, lint, Pyright, and available tests
uv run poe test:unit   # no model downloads, Lean, or API keys
uv run poe --help      # all maintenance tasks
```

Python 3.11+ is supported; `.python-version` selects 3.12 for development.
`uv.lock` pins runtime and dev dependencies. See
[development.md](docs/development.md) and [contributing](docs/CONTRIBUTING.md).
AI agents should read [AGENTS.md](AGENTS.md).

## Documentation

- [Documentation index](docs/README.md)
- [Project overview and module map](docs/PROJECT_OVERVIEW.md)
- [User manual](docs/MVP_USER_MANUAL.md)
- [Local model setup](docs/LOCAL_MODEL_SETUP.md)

The project has not chosen a license yet.
