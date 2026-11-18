"""Stats include standalone requests without counting mining usage twice."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hanno.cli.main import app
from hanno.ledger.models import ExcavationCycle, Seed, now_iso
from hanno.ledger.repository import Ledger


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "stats.db")
    db.init_schema()
    yield db
    db.close()


def run_stats(ledger):
    with patch(
        "hanno.cli.commands.stats.build_context", return_value=SimpleNamespace(ledger=ledger)
    ):
        result = CliRunner().invoke(app, ["stats"])
    assert result.exit_code == 0, result.output
    return result.output


def test_solve_only_usage_and_outstanding_reservations_are_visible(ledger):
    assert ledger.reserve_model_call("solved", "fixture", 0.5, 10)
    ledger.settle_model_call("solved", 0.12, 1000, 200)
    assert ledger.reserve_model_call("in-flight", "fixture", 0.4, 10)
    output = run_stats(ledger)
    assert "Cycles run:            0" in output
    assert "Total cost (estimate): $0.1200" in output
    assert "Outstanding reserves:  $0.4000 (1 requests)" in output
    assert "Today's spend:         $0.5200" in output
    assert "Input tokens used:     1,000" in output
    assert "Output tokens used:    200" in output


def test_usage_combines_legacy_cycles_and_requests_without_double_counting(ledger):
    seed = Seed(text="fixture", domain="mathematics")
    ledger.insert_seed(seed)
    for accounted in (False, True):
        ledger.insert_excavation_cycle(
            ExcavationCycle(
                seed_id=seed.id,
                candidates_requested=2,
                candidates_parsed_ok=1,
                candidates_failed=1,
                total_cost_usd=0.1,
                total_input_tokens=100,
                total_output_tokens=20,
                started_at=now_iso(),
                completed_at=now_iso(),
                usage_accounted=accounted,
            )
        )
    assert ledger.reserve_model_call("mining", "fixture", 0.2, 10)
    ledger.settle_model_call("mining", 0.1, 100, 20)
    assert ledger.reserve_model_call("solve", "fixture", 0.2, 10)
    ledger.settle_model_call("solve", 0.15, 200, 50)
    output = run_stats(ledger)
    assert "Cycles run:            2" in output
    assert "Total cost (estimate): $0.3500" in output
    assert "Input tokens used:     400" in output
    assert "Output tokens used:    90" in output
    assert "Avg tokens/candidate:  60" in output


def test_empty_ledger_reports_zero_usage(ledger):
    output = run_stats(ledger)
    assert "Total cost (estimate): $0.0000" in output
    assert "Input tokens used:     0" in output


def test_old_requests_count_in_totals_but_not_todays_budget(ledger):
    ledger._conn.execute(
        "INSERT INTO model_call_reservations VALUES (?, ?, ?, ?)",
        ("yesterday", "fixture", 1.0, "2000-01-01T00:00:00+00:00"),
    )
    ledger.settle_model_call("yesterday", 0.25, 10, 5)
    output = run_stats(ledger)
    assert "Total cost (estimate): $0.2500" in output
    assert "Today's spend:         $0.0000" in output
