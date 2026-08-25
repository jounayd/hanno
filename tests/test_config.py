"""Config loading and calibration-event auto-logging."""
import json

import pytest
from pydantic import ValidationError

from hanno.config import ExcavatorConfig, HannoConfig, diff_and_log_calibration
from hanno.ledger.repository import Ledger


def _fresh_ledger(tmp_path) -> Ledger:
    ledger = Ledger.connect(tmp_path / "test.db")
    ledger.init_schema()
    return ledger


def test_default_config_loads_with_expected_values():
    config = HannoConfig()
    assert config.excavator.candidates_per_cycle_per_seed == 50
    assert config.excavator.temperatures == [0.3, 0.6, 0.8, 1.0]
    assert config.sieve.pass_threshold == 0.65
    assert config.pipeline.domains_enabled == ["mathematics"]


def test_default_temperatures_are_within_the_api_valid_range():
    # Regression test: the Anthropic API rejects temperature outside 0.0-1.0
    # with a 400 -- a real value of 1.2 shipped in this config once already.
    config = HannoConfig()
    assert all(0.0 <= t <= 1.0 for t in config.excavator.temperatures)


def test_max_tokens_per_candidate_has_enough_headroom_for_iterative_mining():
    # Regression test: 2000 was too tight -- growing already_explored/refined
    # seed context at deeper iterations pushed real responses past the cap,
    # truncating mid-JSON and failing to parse (observed on standard-reasoning
    # arms specifically; deep-reasoning arms already had 8000 and never failed).
    config = HannoConfig()
    assert config.excavator.max_tokens_per_candidate >= 4000


def test_out_of_range_temperature_fails_fast_at_config_load():
    with pytest.raises(ValidationError, match="0.0-1.0"):
        ExcavatorConfig(temperatures=[0.3, 1.2])


def test_first_load_creates_snapshot_and_no_diff_noise(tmp_path):
    ledger = _fresh_ledger(tmp_path)
    config = HannoConfig()
    changes = diff_and_log_calibration(ledger, config)
    assert changes == 0  # nothing to diff against on first run
    assert ledger.get_latest_config_snapshot() is not None
    ledger.close()


def test_changed_parameter_logs_exactly_one_calibration_event(tmp_path):
    ledger = _fresh_ledger(tmp_path)
    config_v1 = HannoConfig()
    diff_and_log_calibration(ledger, config_v1)

    config_v2 = HannoConfig()
    config_v2.excavator.daily_spend_limit_usd = 10.0
    changes = diff_and_log_calibration(ledger, config_v2)

    assert changes == 1
    events = ledger.list_calibration_events()
    matching = [e for e in events if e["parameter"] == "excavator.daily_spend_limit_usd"]
    assert len(matching) == 1
    assert json.loads(matching[0]["old_value"]) == 25.0
    assert json.loads(matching[0]["new_value"]) == 10.0
    ledger.close()


def test_no_change_logs_nothing_on_second_load(tmp_path):
    ledger = _fresh_ledger(tmp_path)
    config = HannoConfig()
    diff_and_log_calibration(ledger, config)
    changes = diff_and_log_calibration(ledger, config)
    assert changes == 0
    ledger.close()
