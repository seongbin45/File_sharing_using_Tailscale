"""File-only logging (no UI). Same %LOCALAPPDATA%\\Naru directory
tsbackup/config.py's config_dir() already uses for the main app's own log
and config.json - not a separate logs/ subfolder, matching that
convention rather than CloneUp's own."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        d = Path(base) / "Naru"
    else:
        d = Path.home() / ".config" / "tsbackup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("tsbackup_update_manager")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    path = log_dir() / "update_manager.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setLevel(logging.WARNING)
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(stream)
    logger.info("log file: %s", path)
    return logger
