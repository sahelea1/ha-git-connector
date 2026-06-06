"""Application entrypoint.

Wires together the option loading, the sync manager, the file watcher, the web
UI and the scheduler loop, and handles graceful shutdown on SIGTERM (sent by
s6 when the add-on stops).
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from .config import Options
from .logsetup import setup_logging
from .manager import SyncManager
from .supervisor import Supervisor
from .watcher import ConfigWatcher
from .web import create_app, serve

_LOGGER = logging.getLogger("gitsync.main")

WEB_HOST = "0.0.0.0"  # noqa: S104 - bound to the internal Ingress network only
WEB_PORT = 8099
LOOP_TICK_SECONDS = 5


class Application:
    def __init__(self) -> None:
        self.options = Options.load()
        setup_logging(self.options.log_level)

        self.supervisor = Supervisor()
        self.manager = SyncManager(self.options, self.supervisor)

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._change_pending = False
        self._change_at = 0.0

        self.watcher: ConfigWatcher | None = None
        self.manager.set_watcher_status_getter(
            lambda: self.watcher.running if self.watcher else False
        )

    # ------------------------------------------------------------- callbacks
    def _on_file_change(self, path: str) -> None:
        self._change_pending = True
        self._change_at = time.monotonic()
        self._wake.set()

    def _handle_signal(self, signum, _frame) -> None:
        _LOGGER.info("Received signal %s — shutting down", signum)
        self._stop.set()
        self._wake.set()

    # --------------------------------------------------------------- startup
    def _start_web(self) -> None:
        app = create_app(self.manager)
        thread = threading.Thread(
            target=serve,
            args=(app, WEB_HOST, WEB_PORT),
            name="web",
            daemon=True,
        )
        thread.start()

    def _start_watcher(self) -> None:
        if not self.options.watch_changes:
            return
        self.watcher = ConfigWatcher(self.options.config_path, self._on_file_change)
        self.watcher.start()

    def _initial_sync(self) -> None:
        if not self.options.configured:
            _LOGGER.warning(
                "No repository URL configured yet. Open the add-on "
                "configuration to get started."
            )
            return
        if not self.options.enabled:
            _LOGGER.info("Automatic synchronisation is disabled in the options")
            # Still make sure the repository is wired up so the UI is useful.
            try:
                self.manager.ensure_setup()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Initial repository setup failed")
            return
        if self.options.restore_on_start:
            _LOGGER.info("restore_on_start is enabled — restoring from remote")
            self.manager.restore(reason="startup")
        else:
            self.manager.sync(reason="startup")

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        _LOGGER.info("GitLab Config Sync starting (config dir: %s)", self.options.config_path)
        self._start_web()
        self._start_watcher()
        self._initial_sync()

        last_periodic = time.monotonic()
        interval = max(0, self.options.sync_interval_minutes) * 60
        debounce = max(1, self.options.watch_debounce_seconds)

        while not self._stop.is_set():
            now = time.monotonic()

            if self.options.enabled and self.options.configured:
                # Periodic backup/sync.
                if interval > 0 and (now - last_periodic) >= interval:
                    self.manager.sync(reason="scheduled")
                    last_periodic = time.monotonic()

                # Debounced change-driven sync.
                if self._change_pending and (now - self._change_at) >= debounce:
                    self._change_pending = False
                    self.manager.sync(reason="file-change")
                    last_periodic = time.monotonic()

            self._wake.wait(timeout=LOOP_TICK_SECONDS)
            self._wake.clear()

        self._shutdown()

    def _shutdown(self) -> None:
        if self.watcher:
            self.watcher.stop()
        _LOGGER.info("GitLab Config Sync stopped")


def main() -> None:
    Application().run()


if __name__ == "__main__":
    main()
