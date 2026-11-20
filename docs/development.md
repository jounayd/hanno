# Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the checkout:

```sh
uv sync --locked
uv run hanno --help
uv run poe check
```

uv installs Python 3.12 (from `.python-version`), the editable package, and development
tools into `.venv`. `uv.lock` pins the resolution. Python 3.11+ remains supported.
PyTorch uses CPU wheels on Linux and Windows so setup does not need CUDA; macOS
uses its native PyPI wheels. See [uv's PyTorch guide](https://docs.astral.sh/uv/guides/integration/pytorch/).

## Checks

| Command | Purpose |
| --- | --- |
| `uv run poe check` | Format check, lint, type check, and all available tests |
| `uv run poe format` | Format Python sources with Ruff |
| `uv run poe format:check` | Check formatting without changing files |
| `uv run poe lint` | Run static lint checks |
| `uv run poe lint:fix` | Apply safe Ruff fixes |
| `uv run poe typecheck` | Run Pyright, also configured for Pylance |
| `uv run poe test:unit` | Run without model downloads or Lean |
| `uv run poe test` | Run every available test and report skipped integrations |
| `uv run poe check:fast` | Run the quality gate without embedding downloads or Lean |
| `uv run poe check:full` | Run the quality gate with Lean prerequisites required |
| `uv run poe test:embeddings` | Run the real embedding integration tests |
| `uv run poe lean:setup` | Download the pinned Lean/Mathlib project and compiled cache |
| `uv run poe lean:check` | Check the locked revisions and compile smoke proofs |
| `uv run poe test:lean` | Require Lean setup and run all Lean integration tests |
| `uv run poe frontend:check` | Check browser scripts with the bundled Node runtime |
| `uv run poe test:coverage` | Report coverage and write `htmlcov/` |
| `uv run poe deps:audit` | Check installed dependencies against the vulnerability service |
| `uv run poe deps:sync` | Install the locked runtime and development tools |
| `uv run poe deps:outdated` | Inspect available dependency updates |
| `uv run poe deps:update` | Refresh the lockfile and sync compatible updates |
| `uv run poe build` | Build a wheel and source distribution into `dist/` |
| `uv run poe package:check` | Build and smoke-test a wheel outside the checkout in a temporary locked environment |
| `uv run poe package:check --lean` | Also test real Lean skills before and after copying skills locally |
| `uv run poe debug:candidates SEED_ID` | Print a seed's candidates from the configured ledger |

Run `uv run poe --help` to list tasks and their help. Poe tasks are the source of truth
for repeatable local checks. Focused tests still accept arguments, for example
`uv run poe test:unit tests/test_stats_cli.py -q`. The diagnostic command also
accepts `--database PATH` and opens an existing ledger read-only.

`uv run poe test:embeddings` loads `sentence-transformers/all-MiniLM-L6-v2`; the first
run downloads it from Hugging Face. After caching it, `HF_HUB_OFFLINE=1 uv run
poe test:embeddings` runs offline. Unit tests use deterministic model fixtures,
never paid model calls.

## Task runner and naming

Poe is the single task runner. It already handles uv environments, command and
Python tasks, arguments, prerequisites, and sequences for this project. A Taskfile
would add another tool and another place to define the same workflows. Revisit
that choice if we adopt a shared build workflow across projects or languages that
benefits from file-based up-to-date checks; the current project does not need it.
See [Poe composition](https://poethepoet.natn.io/guides/composition_guide.html) and
[Task's up-to-date checks](https://taskfile.dev/docs/guide/up-to-date).

Naming is a repository convention, not a delimiter mandated by Poe:

- Keep familiar primary actions short: `check`, `format`, `lint`, `typecheck`, `test`, `build`.
- Use lowercase `family:variant` names for related tasks: `format:check`, `lint:fix`,
  `test:unit`, `test:coverage`, `deps:update`, `lean:setup`, `package:check`.
- Give every public task descriptive help and organize related tasks using
  [Poe's help groups](https://poethepoet.natn.io/guides/help_guide.html#grouping-tasks).
  Grouping changes the help display; commands keep their full names.
- Keep task sequences, docs, and CLI hints aligned when renaming.

`check` runs static checks and available tests, with unavailable Lean integrations
reported as skips. `check:fast` runs static checks and unit tests without model
downloads or Lean. `check:full` requires the pinned Lean project and runs static
checks plus the complete test suite. Packaging and vulnerability audits remain
explicit tasks (`package:check` and `deps:audit`).

## Lean and Mathlib

Install [elan](https://github.com/leanprover/elan#installation) and put its `bin`
directory on PATH, alongside Git. Setup has been checked with elan 4.1.2. Then run:

```sh
uv run poe lean:setup
uv run poe test:lean
uv run poe check:full
```

The tracked `lean/` template selects **Lean 4.36.0**, the matching
[Mathlib release](https://github.com/leanprover-community/mathlib4/tree/v4.36.0)
at `fc4f59e577feb1a4956671bc04ac18aaaf8cd842`, and every transitive Git revision
in `lake-manifest.json`. Setup copies these files into the ignored
`data/mathlib_cache/`, installs the selected toolchain without changing your
global default, and runs `lake exe cache get`. The initial download needs several
GB of disk space and internet access. Cache retrieval follows the
[Mathlib project instructions](https://leanprover-community.github.io/install/project.html).

Setup is repeatable and refuses to overwrite a project with different template
files. Move an older project aside before running it. `lean:check` never downloads
a toolchain: it checks the installed version, package revisions and tracked
changes, then compiles `Smoke.lean` using the cached Mathlib. `test:lean` runs this
preflight first, so a missing integration fails instead of silently skipping.
Ordinary `test` still reports unavailable integrations as skips.

To upgrade deliberately, update `lean/lean-toolchain` and the Mathlib revision
in `lean/lakefile.toml` together. Generate a new manifest with `lake update` in a
separate project, copy the manifest into `lean/`, then run setup and `check:full`
against a fresh project. Do not run `lake update` during normal setup.

## Dependencies and packaging

Use `uv add <package>` for runtime dependencies and `uv add --dev <package>` for
development tools. Commit `pyproject.toml` and `uv.lock` together. For an intentional
refresh, run `uv run poe deps:outdated`, `uv run poe deps:update`,
`uv run poe check:full`, and `uv run poe package:check --lean`. Review and update
the direct dependency minimums when adopting a new baseline; the lockfile pins
the exact development versions. NumPy uses separate minimums for Python 3.11
and 3.12+ because the latest NumPy line requires Python 3.12. Transitive updates
must respect their parent packages' constraints (for example SymPy's mpmath range).

The wheel includes the ledger schema, static frontend, default configuration,
benchmark cases, and shipped skills. Editable configuration stays in `config/`,
`skills/`, and `benchmarks/` in a checkout. Wheels install these defaults under
`share/hanno/` in the Python environment. Runtime databases, logs, and local
settings belong to the working directory and are ignored by Git.

The Lean skills read `lean_verifier.mathlib_cache_path` from Hanno configuration;
relative paths resolve from the working directory, including in wheel installs.
`HANNO_LEAN_VERIFIER__MATHLIB_CACHE_PATH=/path/to/project` selects a different cache.
Shipped Python skills use the application's Python environment, including after
a UI toggle creates editable copies. `package:check --lean` needs the prepared
Lean project and tests both bundled and copied skill scripts. Package checks
keep the current development installation unchanged and delete their temporary
environment afterward.

CI configuration is deferred. The local Poe commands cover static checks, unit
tests, embedding and Lean integrations, and installed-wheel smoke checks.

The project has no chosen license yet. Do not publish a release or add a license
on the owner's behalf as part of routine setup.

## Verification and budget notes

Complete Lean proofs must retain the original theorem header (including its
hypotheses and imports). The compiler audits transitive axiom dependencies and
accepts only Lean's standard `propext`, `Classical.choice`, and `Quot.sound` axioms.
Proofs relying on `sorryAx` or an added axiom remain unresolved. Tactics that add
other trusted axioms, including native evaluation, must be replaced with ordinary
proof-producing tactics. This does not establish that the original informal
claim was translated faithfully; that still needs review.

The Anthropic budget reserves an upper-bound estimate before every HTTP request,
including tool rounds, and settles with returned token usage. Parallel clients
share reservations through the ledger. A failed or cancelled request with unknown
billing retains its reservation until the UTC day ends. The price table is an
estimate, not a provider billing guarantee; OpenAI-compatible pricing remains
unknown and is not capped. `hanno stats` reports settled request costs and tokens,
including standalone solve and verification calls, plus legacy cycle usage without
double-counting new cycles. It shows unsettled reservations separately and includes
today's reservations in its daily estimate (UTC). Standalone OpenAI-compatible calls
currently have no request-level usage records; their mining cycles still contribute
tokens to the summary.

Tool invocations now record their pipeline phase. Existing rows migrate as
`generation`; discoveries without an explicit completed verification may need
one verification run after upgrading. The model-call reservation and settlement
tables are append-only and are included in ledger exports.
