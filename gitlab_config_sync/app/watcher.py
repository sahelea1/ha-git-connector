"""Filesystem watcher.

Watches the Home Assistant configuration directory and invokes a callback when
a *relevant* file changes. Noisy, ignored paths (database, logs, ``.storage``,
the ``.git`` directory itself …) are filtered out so we do not trigger a sync
for churn that is never committed anyway.

The callback is expected to be cheap (it just signals the scheduler); the
actual debouncing happens in the main loop.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_LOGGER = logging.getLogger("gitsync.watcher")

# Directory names anywhere in the path that should be ignored.
_IGNORED_DIRS = {
    ".git", ".storage", ".cloud", "deps", "tts", "backups",
    "__pycache__", "tmp",
}
# File suffixes that should be ignored.
_IGNORED_SUFFIXES = (
    ".db", ".db-shm", ".db-wal", ".db-journal", ".log", ".pyc", ".swp", "~",
)


def _is_relevant(path: str) -> bool:
    parts = set(path.split(os.sep))
    if parts & _IGNORED_DIRS:
        return False
    if path.endswith(_IGNORED_SUFFIXES):
        return False
    return True


class _Handler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change

    def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog signature
        if event.is_directory:
            return
        path = getattr(event, "dest_path", "") or event.src_path
        if _is_relevant(path):
            self._on_change(path)


class ConfigWatcher:
    def __init__(self, path: str, on_change: Callable[[str], None]) -> None:
        self._path = path
        self._on_change = on_change
        self._observer: Observer | None = None

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def start(self) -> None:
        if self.running:
            return
        try:
            observer = Observer()
            observer.schedule(_Handler(self._on_change), self._path, recursive=True)
            observer.start()
            self._observer = observer
            _LOGGER.info("Watching %s for changes", self._path)
        except Exception as err:  # pragma: no cover - environment dependent
            _LOGGER.warning("Could not start file watcher: %s", err)
            self._observer = None

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
