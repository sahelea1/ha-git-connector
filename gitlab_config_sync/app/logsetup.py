"""Logging configuration.

Logs go to stdout (so they appear in the Home Assistant add-on log) and are
also captured in an in-memory ring buffer that the web UI can display.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Deque

# Map Home Assistant / bashio log levels onto Python logging levels.
_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}

_RING: Deque[str] = deque(maxlen=500)


class _RingHandler(logging.Handler):
    """A logging handler that keeps the most recent records in memory."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING.append(self.format(record))
        except Exception:  # pragma: no cover - logging must never raise
            pass


def setup_logging(level: str = "info") -> None:
    log_level = _LEVELS.get(str(level).lower(), logging.INFO)
    fmt = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(log_level)
    # Reset handlers so reconfiguration is idempotent.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    ring = _RingHandler()
    ring.setFormatter(fmt)
    root.addHandler(ring)

    # Quieten noisy third-party loggers.
    logging.getLogger("waitress").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)


def recent_logs(limit: int = 200) -> list[str]:
    """Return the most recent log lines (oldest first)."""
    if limit <= 0 or limit >= len(_RING):
        return list(_RING)
    return list(_RING)[-limit:]
