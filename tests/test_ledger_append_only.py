"""Proves append-only enforcement: DB triggers reject UPDATE/DELETE on every
table, and the Ledger class exposes no update_*/delete_* method."""
import sqlite3

import pytest

from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger

ALL_TABLES = Ledger.TABLES


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


def test_no_mutation_methods_on_ledger_class():
    method_names = [name for name in dir(Ledger) if not name.startswith("_")]
    forbidden_prefixes = ("update_", "delete_")
    violations = [m for m in method_names if m.startswith(forbidden_prefixes)]
    assert violations == [], f"Ledger exposes mutation methods: {violations}"


def test_seeds_table_rejects_update(ledger):
    seed = Seed(text="a" * 30, domain="mathematics")
    ledger.insert_seed(seed)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute("UPDATE seeds SET text = 'x' WHERE id = ?", (seed.id,))


def test_seeds_table_rejects_delete(ledger):
    seed = Seed(text="a" * 30, domain="mathematics")
    ledger.insert_seed(seed)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute("DELETE FROM seeds WHERE id = ?", (seed.id,))


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_table_has_append_only_triggers(ledger, table):
    triggers = ledger._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name = ?", (table,)
    ).fetchall()
    trigger_names = {row["name"] for row in triggers}
    assert f"{table}_no_update" in trigger_names
    assert f"{table}_no_delete" in trigger_names


def test_insert_and_query_seed(ledger):
    seed = Seed(text="a" * 30, domain="mathematics")
    ledger.insert_seed(seed)
    fetched = ledger.get_seed(seed.id)
    assert fetched["id"] == seed.id
    assert fetched["text"] == seed.text


def test_export_produces_valid_json(ledger, tmp_path):
    seed = Seed(text="a" * 30, domain="mathematics")
    ledger.insert_seed(seed)
    out_path = tmp_path / "export.json"
    ledger.export_json(out_path)
    import json
    with open(out_path) as f:
        data = json.load(f)
    assert "seeds" in data
    assert any(s["id"] == seed.id for s in data["seeds"])
