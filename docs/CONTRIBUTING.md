# Contributing

Start with [development.md](development.md) and run `uv sync --locked`.

Keep a change focused on a concrete behavior or maintenance need. Add a regression
test for a bug, update affected documentation, and run `uv run poe check` before
requesting review. State any unavailable Lean or embedding integrations explicitly.
Use deterministic model responses in tests; do not require credentials or make paid calls.

The ledger is append-only. New state needs a new event; schema changes must preserve
existing databases. Model output is untrusted, including generated Lean and Python.
Do not equate a compiling file with a faithful, complete proof.

Pull requests should explain the problem, resulting behavior, and validation. Keep
formatting or structural changes identifiable from behavior changes. Use Conventional
Commits when committing. Never include keys, local ledgers, cached models, or generated reports.

Agent-specific guidance is in [AGENTS.md](../AGENTS.md).
