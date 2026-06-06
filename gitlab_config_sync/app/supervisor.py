"""Thin client for the Home Assistant Core REST API.

The add-on is granted ``homeassistant_api: true`` which means the Supervisor
exposes the Core API at ``http://supervisor/core/api`` and provides a
``SUPERVISOR_TOKEN`` environment variable for authentication.

These calls are only used to optionally validate the configuration and to
reload/restart Home Assistant after a restore.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

_LOGGER = logging.getLogger("gitsync.supervisor")

_BASE_URL = "http://supervisor/core/api"


@dataclass
class CheckResult:
    ok: bool
    errors: str = ""


class Supervisor:
    def __init__(self) -> None:
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")

    @property
    def available(self) -> bool:
        return bool(self._token)

    # ------------------------------------------------------------------ helper
    def _post(self, path: str, timeout: float = 30.0) -> tuple[int, dict]:
        url = f"{_BASE_URL}{path}"
        request = urllib.request.Request(url, data=b"", method="POST")
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                payload = {"raw": body}
            return response.status, payload

    # ----------------------------------------------------------------- actions
    def check_config(self) -> CheckResult:
        """Validate the Home Assistant configuration."""
        if not self.available:
            return CheckResult(ok=True, errors="supervisor api unavailable")
        try:
            status, payload = self._post("/config/core/check_config", timeout=120.0)
        except (urllib.error.URLError, OSError) as err:
            _LOGGER.warning("Configuration check failed to run: %s", err)
            return CheckResult(ok=False, errors=str(err))
        result = str(payload.get("result", "")).lower()
        if status == 200 and result == "valid":
            return CheckResult(ok=True)
        return CheckResult(ok=False, errors=str(payload.get("errors") or payload))

    def reload_all(self) -> bool:
        """Reload all YAML configuration without a full restart."""
        return self._fire("/services/homeassistant/reload_all", "reload")

    def restart(self) -> bool:
        """Restart Home Assistant Core."""
        # The connection is usually dropped while Core restarts, so a transport
        # error here is expected and treated as success.
        try:
            self._post("/services/homeassistant/restart", timeout=10.0)
            return True
        except (urllib.error.URLError, OSError):
            return True

    def _fire(self, path: str, label: str) -> bool:
        if not self.available:
            _LOGGER.warning("Cannot %s: Supervisor API unavailable", label)
            return False
        try:
            status, _ = self._post(path)
            if status in (200, 201):
                return True
            _LOGGER.warning("%s returned HTTP %s", label, status)
            return False
        except (urllib.error.URLError, OSError) as err:
            _LOGGER.warning("%s failed: %s", label, err)
            return False
