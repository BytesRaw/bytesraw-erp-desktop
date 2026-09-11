"""Logging configuration.

Logs go to a rotating file under the app data directory and, when a console is
attached, to stderr. QtWebEngine's own chatter is demoted to WARNING because a
Chromium build emits a great deal of routine noise on startup.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from bytesraw_erp.core.paths import logs_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:  # idempotent: tests and re-entry must not double-log
        return
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        logs_dir() / "bytesraw-erp.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(stream_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
