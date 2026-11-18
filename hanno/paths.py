"""Locate editable checkout resources or the copies installed by a wheel."""

import sysconfig
from pathlib import Path


def resource_path(relative: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    checkout = root / relative
    if (root / "pyproject.toml").is_file() and checkout.exists():
        return checkout
    return Path(sysconfig.get_path("data")) / "share" / "hanno" / relative
