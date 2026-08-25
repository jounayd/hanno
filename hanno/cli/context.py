"""Shared CLI bootstrap: load config, init ledger schema, log calibration diffs."""
from __future__ import annotations

from dataclasses import dataclass, field

from hanno.config import HannoConfig, diff_and_log_calibration, load_config
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.repository import Ledger
from hanno.logging_setup import setup_logging
from hanno.skills.registry import SkillDefinition, load_skills


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
        config=config, ledger=ledger, fingerprint_engine=fingerprint_engine, skills=skills,
    )
