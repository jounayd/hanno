"""Regression test for the exact failure just hit in real use: a
data/hanno.db created before a schema change (new columns added to an
existing table) must self-heal on the next init_schema() call, WITHOUT
losing any existing rows. CREATE TABLE IF NOT EXISTS alone can't do this --
it only handles brand-new tables, never new columns on an existing one.
"""

import sqlite3

from hanno.ledger import db as db_module
from hanno.ledger.repository import Ledger


def test_missing_candidate_columns_are_added_without_losing_data(tmp_path):
    db_path = tmp_path / "old_schema.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # A stripped-down "old" candidates table, as if created before
    # reasoning_effort / self_rated_novelty / path_score / numeric_check
    # existed -- exactly the shape the real data/hanno.db was in.
    conn.execute("""
        CREATE TABLE candidates (
            id TEXT PRIMARY KEY,
            seed_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            temperature REAL NOT NULL,
            prompt_variant TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            core_claim TEXT,
            formal_statement TEXT,
            intuition TEXT,
            connections TEXT,
            self_assessed_novelty TEXT,
            identified_weaknesses TEXT,
            parse_status TEXT NOT NULL,
            parse_error TEXT,
            duplicate_status TEXT,
            exploration_signal TEXT,
            suggested_seeds TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            generated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO candidates (id, seed_id, domain, temperature, prompt_variant, model, "
        "prompt_text, raw_text, core_claim, parse_status, generated_at) "
        "VALUES ('c1', 's1', 'mathematics', 0.3, 'direct_conjecture', 'test-model', "
        "'a prompt', 'a response', 'an existing real claim', 'ok', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    ledger = Ledger.connect(db_path)
    ledger.init_schema()  # must not raise, must not touch existing rows

    row = ledger._conn.execute("SELECT * FROM candidates WHERE id = 'c1'").fetchone()
    assert row["core_claim"] == "an existing real claim"  # pre-existing data survived
    assert row["reasoning_effort"] is None  # backfilled as NULL, not dropped/errored
    assert row["path_score"] is None
    assert row["numeric_check_result"] is None

    # And the columns are now genuinely usable -- an insert exercising them
    # via the real repository method must succeed.
    from hanno.ledger.models import Candidate

    ledger.insert_candidate(
        Candidate(
            seed_id="s1",
            domain="mathematics",
            temperature=0.5,
            prompt_variant="direct_conjecture",
            model="test-model",
            prompt_text="p",
            raw_text="r",
            parse_status="ok",
            reasoning_effort="standard",
            self_rated_novelty=0.5,
            self_rated_rigor=0.5,
            path_score=0.5,
        )
    )
    ledger.close()


def test_init_schema_is_safe_to_call_repeatedly_on_an_already_migrated_db(tmp_path):
    db_path = tmp_path / "fresh.db"
    ledger = Ledger.connect(db_path)
    ledger.init_schema()
    ledger.init_schema()  # second call must be a true no-op, not an error
    ledger.close()


def test_migrate_columns_skips_tables_that_dont_exist_yet(tmp_path):
    # A completely empty database -- no tables at all yet. The migration
    # step must not error just because none of its target tables exist
    # (schema.sql's CREATE TABLE runs first and handles that case).
    db_path = tmp_path / "empty.db"
    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='candidates'"
        ).fetchone()
        is not None
    )
    conn.close()
