"""Add-on option handling.

Home Assistant writes the validated user options to ``/data/options.json``.
We read that file, fall back to sane defaults and expose everything through a
single :class:`Options` object that the rest of the application consumes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

OPTIONS_FILE = os.environ.get("GITSYNC_OPTIONS", "/data/options.json")
DATA_DIR = os.environ.get("GITSYNC_DATA", "/data")

# Candidate locations for the Home Assistant configuration directory. Modern
# add-ons that map ``homeassistant_config`` receive it at ``/homeassistant``;
# the legacy mapping exposed it at ``/config``.
CONFIG_PATH_CANDIDATES = ("/homeassistant", "/config")

DEFAULTS: dict[str, Any] = {
    "repository_url": "",
    "branch": "prod",
    "dev_branch": "dev",
    "allow_branch_switch": True,
    "auth_method": "token",
    "username": "",
    "token": "",
    "ssh_key": "",
    "commit_name": "Home Assistant",
    "commit_email": "homeassistant@local",
    "commit_message": "Home Assistant config backup",
    "enabled": True,
    "sync_strategy": "rebase",
    "sync_interval_minutes": 60,
    "watch_changes": True,
    "watch_debounce_seconds": 30,
    "restore_on_start": False,
    "restore_clean": False,
    "apply_action": "none",
    "check_config_before_apply": True,
    "verify_ssl": True,
    "excludes": [],
    "log_level": "info",
}


@dataclass
class Options:
    """Typed view over the add-on options."""

    repository_url: str = ""
    branch: str = "prod"
    dev_branch: str = "dev"
    allow_branch_switch: bool = True
    auth_method: str = "token"
    username: str = ""
    token: str = ""
    ssh_key: str = ""
    commit_name: str = "Home Assistant"
    commit_email: str = "homeassistant@local"
    commit_message: str = "Home Assistant config backup"
    enabled: bool = True
    sync_strategy: str = "rebase"
    sync_interval_minutes: int = 60
    watch_changes: bool = True
    watch_debounce_seconds: int = 30
    restore_on_start: bool = False
    restore_clean: bool = False
    apply_action: str = "none"
    check_config_before_apply: bool = True
    verify_ssl: bool = True
    excludes: list[str] = field(default_factory=list)
    log_level: str = "info"

    # ----------------------------------------------------------------- loaders
    @classmethod
    def load(cls) -> "Options":
        data = dict(DEFAULTS)
        try:
            with open(OPTIONS_FILE, "r", encoding="utf-8") as handle:
                user = json.load(handle)
            if isinstance(user, dict):
                data.update({k: v for k, v in user.items() if k in DEFAULTS})
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # Corrupt options file — fall back to defaults rather than crashing.
            pass
        return cls(**data)  # type: ignore[arg-type]

    # -------------------------------------------------------------- properties
    @property
    def config_path(self) -> str:
        """Return the Home Assistant configuration directory to manage."""
        override = os.environ.get("GITSYNC_CONFIG_PATH")
        if override:
            return override
        for candidate in CONFIG_PATH_CANDIDATES:
            if os.path.isdir(candidate):
                return candidate
        # Default to the modern mapping even if it does not exist yet.
        return CONFIG_PATH_CANDIDATES[0]

    @property
    def data_dir(self) -> str:
        return DATA_DIR

    @property
    def configured(self) -> bool:
        return bool(self.repository_url.strip())

    @property
    def prod_branch(self) -> str:
        """The stable/source-of-truth branch (alias for ``branch``)."""
        return self.branch

    @property
    def effective_username(self) -> str:
        """Username to present for HTTPS token authentication.

        GitLab accepts any non-empty username together with a personal/project
        access token used as the password; ``oauth2`` is the conventional value.
        """
        return self.username.strip() or "oauth2"

    @property
    def safe_repository_url(self) -> str:
        """Repository URL with any embedded credentials stripped, for display."""
        url = self.repository_url.strip()
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            if "@" in rest:
                rest = rest.split("@", 1)[1]
            return f"{scheme}://{rest}"
        return url
