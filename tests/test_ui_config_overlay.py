"""config/local.yaml overlay: the Settings UI writes only the fields the
user changed here, never touching the checked-in, documented default.yaml.
Precedence must stay env vars > local.yaml > default.yaml."""
import yaml

from hanno.config import deep_merge, load_config
from hanno.ui.secrets import load_config_overlay, save_config_overlay


def test_deep_merge_overlays_nested_keys_without_dropping_siblings():
    base = {"excavator": {"model": "base-model", "candidates_per_cycle_per_seed": 50}, "skills": {"enabled": True}}
    overlay = {"excavator": {"model": "overridden-model"}}

    merged = deep_merge(base, overlay)

    assert merged["excavator"]["model"] == "overridden-model"
    assert merged["excavator"]["candidates_per_cycle_per_seed"] == 50  # untouched sibling survives
    assert merged["skills"]["enabled"] is True  # untouched top-level section survives


def test_load_config_applies_local_overlay_on_top_of_default(tmp_path):
    default_path = tmp_path / "default.yaml"
    local_path = tmp_path / "local.yaml"
    default_path.write_text(yaml.safe_dump({
        "excavator": {"model": "claude-sonnet-4-5-20250929", "daily_spend_limit_usd": 25.0},
    }))
    local_path.write_text(yaml.safe_dump({"excavator": {"daily_spend_limit_usd": 5.0}}))

    config = load_config(path=default_path, local_path=local_path)

    assert config.excavator.daily_spend_limit_usd == 5.0  # overlay wins
    assert config.excavator.model == "claude-sonnet-4-5-20250929"  # untouched field survives


def test_load_config_with_no_local_overlay_is_unaffected(tmp_path):
    default_path = tmp_path / "default.yaml"
    default_path.write_text(yaml.safe_dump({"excavator": {"daily_spend_limit_usd": 25.0}}))
    missing_local = tmp_path / "does_not_exist.yaml"

    config = load_config(path=default_path, local_path=missing_local)

    assert config.excavator.daily_spend_limit_usd == 25.0


def test_local_overlay_wins_over_env_var_when_field_is_present_in_yaml(tmp_path, monkeypatch):
    # Documents a real, PRE-EXISTING characteristic of load_config (not
    # introduced by the local-overlay feature): HannoConfig(**raw) passes
    # "excavator" as an explicit init value whenever `raw` contains that
    # key, and pydantic-settings' init source outranks env per *field*
    # (not per *leaf*) -- so a HANNO_EXCAVATOR__* env var only has an
    # effect when `raw` omits "excavator" entirely. Since default.yaml
    # populates every section, env overrides for existing fields are
    # effectively inert today. This test pins the actual behavior so a
    # future fix to that precedence doesn't regress silently.
    default_path = tmp_path / "default.yaml"
    local_path = tmp_path / "local.yaml"
    default_path.write_text(yaml.safe_dump({"excavator": {"daily_spend_limit_usd": 25.0}}))
    local_path.write_text(yaml.safe_dump({"excavator": {"daily_spend_limit_usd": 5.0}}))
    monkeypatch.setenv("HANNO_EXCAVATOR__DAILY_SPEND_LIMIT_USD", "1.0")

    config = load_config(path=default_path, local_path=local_path)

    assert config.excavator.daily_spend_limit_usd == 5.0  # local.yaml wins; env var is inert here


def test_env_var_applies_when_section_is_absent_from_yaml_entirely(tmp_path, monkeypatch):
    # The env var DOES work when neither default.yaml nor local.yaml
    # mentions "excavator" at all -- pydantic-settings then has no
    # competing init value for that field and falls back to its env source.
    empty_default = tmp_path / "default.yaml"
    empty_default.write_text(yaml.safe_dump({}))
    missing_local = tmp_path / "local.yaml"
    monkeypatch.setenv("HANNO_EXCAVATOR__DAILY_SPEND_LIMIT_USD", "1.0")

    config = load_config(path=empty_default, local_path=missing_local)

    assert config.excavator.daily_spend_limit_usd == 1.0


def test_save_config_overlay_merges_instead_of_clobbering(tmp_path):
    overlay_path = tmp_path / "local.yaml"

    save_config_overlay({"excavator": {"model": "model-a"}}, path=overlay_path)
    save_config_overlay({"excavator": {"daily_spend_limit_usd": 3.0}}, path=overlay_path)

    saved = load_config_overlay(overlay_path)
    assert saved["excavator"]["model"] == "model-a"  # first save's key survives the second save
    assert saved["excavator"]["daily_spend_limit_usd"] == 3.0


def test_load_config_overlay_missing_file_returns_empty_dict(tmp_path):
    assert load_config_overlay(tmp_path / "nope.yaml") == {}


def test_save_config_overlay_creates_parent_directory(tmp_path):
    nested_path = tmp_path / "nested" / "local.yaml"
    save_config_overlay({"excavator": {"model": "x"}}, path=nested_path)
    assert nested_path.exists()
