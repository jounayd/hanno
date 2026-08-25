"""Structured logging: console (human-readable) + rotating file (JSON lines)."""
from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from hanno.config import LoggingConfig


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / config.log_file

    root = logging.getLogger("hanno")
    root.setLevel(config.level)
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"hanno.{name}")
