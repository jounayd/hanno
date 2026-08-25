"""API-key storage for the Settings UI: data/secrets.local.yaml, gitignored,
never written to config/default.yaml or config/local.yaml. Read-only-ish by
design -- callers get the full key exactly once (to construct a client) or
a masked status ({set, last4}), never the full value round-tripped back to
a browser after it's been saved.

Also provides the config/local.yaml overlay writer used by the Settings
form for every OTHER (non-secret) config field -- reuses hanno.config's
deep_merge so partial saves don't clobber previously saved settings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hanno.config import LOCAL_CONFIG_PATH, deep_merge

DEFAULT_SECRETS_PATH = "data/secrets.local.yaml"


def _field_name(provider: str) -> str:
    # "anthropic_api_key" is the pre-existing field name from before
    # multi-provider support -- kept as-is for provider="anthropic" so
    # existing saved keys keep working without a migration.
    return f"{provider}_api_key"


def load_api_key(provider: str = "anthropic", path: str | Path = DEFAULT_SECRETS_PATH) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    key = data.get(_field_name(provider))
    return key or None


def save_api_key(provider: str, api_key: str, path: str | Path = DEFAULT_SECRETS_PATH) -> None:
    path = Path(path)
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            existing = yaml.safe_load(f) or {}
    existing[_field_name(provider)] = api_key
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(existing, f)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort; not every filesystem supports POSIX permissions


def api_key_status(provider: str = "anthropic", path: str | Path = DEFAULT_SECRETS_PATH) -> dict[str, Any]:
    key = load_api_key(provider, path)
    if not key:
        return {"set": False, "last4": None}
    return {"set": True, "last4": key[-4:] if len(key) >= 4 else key}


def save_config_overlay(updates: dict[str, Any], path: str | Path = LOCAL_CONFIG_PATH) -> None:
    """Merge `updates` (a possibly-nested dict matching HannoConfig's shape,
    e.g. {"excavator": {"model": "..."}}) into config/local.yaml, preserving
    any previously saved keys not present in this update."""
    path = Path(path)
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            existing = yaml.safe_load(f) or {}
    merged = deep_merge(existing, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(merged, f, sort_keys=False)


def load_config_overlay(path: str | Path = LOCAL_CONFIG_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}
