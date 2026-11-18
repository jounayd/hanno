"""Shared CLI bootstrap: load config, init ledger schema, log calibration diffs."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar

import typer

from hanno.config import HannoConfig, diff_and_log_calibration, load_config
from hanno.excavator.spend_tracker import SpendLimitExceeded
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.repository import Ledger
from hanno.logging_setup import setup_logging
from hanno.skills.registry import SkillDefinition, load_skills

_T = TypeVar("_T")


def run_async(work: Coroutine[Any, Any, _T]) -> _T:
    try:
        return asyncio.run(work)
    except SpendLimitExceeded as error:
        typer.secho(f"HALTED: {error}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from error


@dataclass
class AppContext:
    config: HannoConfig
    ledger: Ledger
    fingerprint_engine: FingerprintEngine
    skills: list[SkillDefinition] = field(default_factory=list)


def build_context() -> AppContext:
    config = load_config()
    setup_logging(config.logging)
    ledger = Ledger.connect(config.ledger.database_path)
    ledger.init_schema()
    diff_and_log_calibration(ledger, config)
    fingerprint_engine = FingerprintEngine(ledger, config.fingerprint)
    skills = load_skills(config.skills.directory) if config.skills.enabled else []
    return AppContext(
        config=config,
        ledger=ledger,
        fingerprint_engine=fingerprint_engine,
        skills=skills,
    )
