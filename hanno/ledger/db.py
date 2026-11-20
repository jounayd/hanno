"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# CREATE TABLE IF NOT EXISTS (in schema.sql) only covers brand-new tables --
# it never adds a column to a table that already exists on disk from an
# earlier version of schema.sql. Every column added to an EXISTING table
# after its first release must get an entry here, or existing databases
# break with "no such column" the next time that column is used. New
# columns must be nullable (or have a DEFAULT) since they're being added to
# a table that may already have rows.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("excavation_cycles", "usage_accounted", "INTEGER NOT NULL DEFAULT 0"),
    ("tool_invocations", "phase", "TEXT NOT NULL DEFAULT 'generation'"),
    ("candidates", "prompt_text", "TEXT NOT NULL DEFAULT ''"),
    ("candidates", "exploration_signal", "TEXT"),
    ("candidates", "suggested_seeds", "TEXT"),
    ("candidates", "input_tokens", "INTEGER"),
    ("candidates", "output_tokens", "INTEGER"),
    ("candidates", "reasoning_effort", "TEXT"),
    ("candidates", "self_rated_novelty", "REAL"),
    ("candidates", "self_rated_rigor", "REAL"),
    ("candidates", "path_score", "REAL"),
    ("candidates", "numeric_check", "TEXT"),
    ("candidates", "numeric_check_result", "INTEGER"),
    ("candidates", "steering_hint", "TEXT"),
    ("candidates", "claim_type", "TEXT"),
    ("candidates", "iteration", "INTEGER NOT NULL DEFAULT 1"),
    ("candidates", "refined_from_candidate_id", "TEXT REFERENCES candidates(id)"),
    ("excavation_cycles", "total_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("excavation_cycles", "total_output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("excavation_cycles", "iteration", "INTEGER NOT NULL DEFAULT 1"),
    ("excavation_cycles", "refined_from_candidate_id", "TEXT REFERENCES candidates(id)"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_columns(conn: sqlite3.Connection) -> None:
    existing_tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table, column, definition in _COLUMN_MIGRATIONS:
        if table not in existing_tables:
            continue  # a brand-new table already has every column via CREATE TABLE
        if column not in _existing_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit off explicit txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: safe to call on every startup."""
    with open(SCHEMA_PATH) as f:
        script = f.read()
    conn.executescript(script)
    _migrate_columns(conn)
