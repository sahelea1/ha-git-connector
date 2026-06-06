"""Synchronisation orchestration.

:class:`SyncManager` owns the git repository, decides what to do for a given
sync/backup/restore request and records state for the UI. All git access is
serialised through a single re-entrant lock so the scheduler, the file watcher
and the web UI can never run two operations at once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from .config import Options
from .gitrepo import GitError, GitRepo, SyncConflict
from .supervisor import Supervisor

_LOGGER = logging.getLogger("gitsync.manager")

# Managed .gitignore block. Everything between the markers is owned by the
# add-on; any content the user adds outside the markers is preserved.
_IGNORE_BEGIN = "# === GitLab Config Sync (managed — do not edit inside this block) ==="
_IGNORE_END = "# === end GitLab Config Sync ==="

_BASELINE_IGNORE = [
    "# Home Assistant runtime database",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.db-journal",
    "home-assistant_v2.db*",
    "",
    "# Logs",
    "*.log",
    "*.log.*",
    "home-assistant.log*",
    "OZW_Log.txt",
    "",
    "# Sensitive runtime state (auth tokens, integration credentials, registries)",
    ".storage/",
    ".cloud/",
    ".uuid",
    "ip_bans.yaml",
    "",
    "# Caches and dependencies",
    "__pycache__/",
    "*.pyc",
    "deps/",
    "tts/",
    "tmp/",
    ".HA_VERSION",
    "",
    "# Backups and archives",
    "backups/",
    "*.tar",
    "*.tar.gz",
    "",
    "# OS noise",
    ".DS_Store",
    "Thumbs.db",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncManager:
    def __init__(self, options: Options, supervisor: Supervisor) -> None:
        self.options = options
        self.supervisor = supervisor
        self._lock = threading.RLock()
        self._state_file = os.path.join(options.data_dir, "state.json")
        self._initial_restore_done = False
        self._watcher_running_getter = lambda: False

        self.repo = GitRepo(
            path=options.config_path,
            auth_method=options.auth_method,
            url=options.repository_url,
            username=options.effective_username,
            token=options.token,
            ssh_key=options.ssh_key,
            verify_ssl=options.verify_ssl,
            user_name=options.commit_name,
            user_email=options.commit_email,
            data_dir=options.data_dir,
        )

        self._state: dict[str, Any] = self._load_state()

    # ------------------------------------------------------------------ wiring
    def set_watcher_status_getter(self, getter) -> None:
        self._watcher_running_getter = getter

    # ------------------------------------------------------------- persistence
    def _load_state(self) -> dict[str, Any]:
        try:
            with open(self._state_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            with open(self._state_file, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle)
        except OSError:
            pass

    def _record(self, result: str, reason: str, message: str = "", applied: str = "") -> None:
        self._state.update(
            {
                "last_result": result,
                "last_reason": reason,
                "last_message": message,
                "last_run": _now_iso(),
                "last_applied": applied,
            }
        )
        self._save_state()

    # -------------------------------------------------------------- gitignore
    def _write_gitignore(self) -> None:
        path = os.path.join(self.options.config_path, ".gitignore")
        managed = [_IGNORE_BEGIN, *_BASELINE_IGNORE]
        if self.options.excludes:
            managed += ["", "# User-defined excludes"]
            managed += [pattern for pattern in self.options.excludes if pattern.strip()]
        managed.append(_IGNORE_END)
        managed_block = "\n".join(managed) + "\n"

        preserved = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()
            preserved = self._strip_managed_block(existing)

        content = managed_block
        if preserved.strip():
            content = managed_block + "\n" + preserved.lstrip("\n")

        # Avoid rewriting (and dirtying the tree) when nothing changed.
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == content:
                    return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    @staticmethod
    def _strip_managed_block(text: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        skipping = False
        for line in lines:
            if line.strip() == _IGNORE_BEGIN:
                skipping = True
                continue
            if line.strip() == _IGNORE_END:
                skipping = False
                continue
            if not skipping:
                out.append(line)
        return "\n".join(out)

    # ------------------------------------------------------------------- setup
    def ensure_setup(self) -> bool:
        """Initialise/repair the repository. Returns True if an initial restore
        from the remote was performed (i.e. local files changed)."""
        opts = self.options
        self.repo.prepare_ssh_key()

        first_run = not self.repo.initialized
        if first_run:
            _LOGGER.info("Initialising git repository in %s", opts.config_path)
            self.repo.init(opts.branch)

        self.repo.set_identity()
        self.repo.set_remote()
        self._write_gitignore()

        initial_restore = False
        if first_run:
            # Try to adopt an existing remote branch so a freshly installed
            # Home Assistant picks up the known-good configuration.
            try:
                self.repo.fetch()
                if self.repo.remote_branch_exists(opts.branch):
                    _LOGGER.info(
                        "Existing '%s' branch found on remote — adopting it", opts.branch
                    )
                    self.repo.checkout_force(
                        opts.branch, f"origin/{opts.branch}"
                    )
                    self.repo.set_upstream(opts.branch)
                    if opts.restore_clean:
                        self.repo.clean(opts.excludes)
                    initial_restore = True
            except GitError as err:
                _LOGGER.warning(
                    "Could not contact remote during setup (working offline "
                    "for now): %s", err
                )
        else:
            self._ensure_branch()

        return initial_restore

    def _ensure_branch(self) -> None:
        """Make sure the configured branch is checked out for an existing repo."""
        opts = self.options
        current = self.repo.current_branch()
        if current == opts.branch:
            return
        try:
            self.repo.fetch()
        except GitError:
            pass
        try:
            if self.repo.remote_branch_exists(opts.branch):
                self.repo.checkout_force(opts.branch, f"origin/{opts.branch}")
            else:
                self.repo.checkout_force(opts.branch)
            self.repo.set_upstream(opts.branch)
            _LOGGER.info("Switched to branch '%s'", opts.branch)
        except GitError as err:
            _LOGGER.error("Could not switch to branch '%s': %s", opts.branch, err)

    # ------------------------------------------------------------------- apply
    def _maybe_apply(self) -> str:
        """Optionally validate and reload/restart Home Assistant after a change."""
        action = self.options.apply_action
        if action == "none":
            return ""
        if self.options.check_config_before_apply:
            result = self.supervisor.check_config()
            if not result.ok:
                _LOGGER.error(
                    "Configuration check failed — skipping %s. %s",
                    action, result.errors,
                )
                return f"skipped:{action} (invalid config)"
            _LOGGER.info("Configuration check passed")
        if action == "reload":
            ok = self.supervisor.reload_all()
            _LOGGER.info("Reloaded Home Assistant configuration" if ok else "Reload failed")
            return "reloaded" if ok else "reload-failed"
        if action == "restart":
            _LOGGER.info("Restarting Home Assistant Core")
            self.supervisor.restart()
            return "restarted"
        return ""

    def _commit_message(self) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"{self.options.commit_message} — {stamp}"

    # ------------------------------------------------------------------- sync
    def sync(self, reason: str = "manual") -> dict[str, Any]:
        """Two-way (or strategy-specific) synchronisation."""
        if not self.options.configured:
            self._record("idle", reason, "No repository URL configured")
            return self._state
        with self._lock:
            try:
                applied_local = self.ensure_setup()
                changed = self._do_sync()
                changed = changed or applied_local or self._initial_restore_done
                self._initial_restore_done = False
                applied = self._maybe_apply() if changed else ""
                self._record("ok", reason, "Synchronised", applied)
                _LOGGER.info("Sync complete (%s)", reason)
            except SyncConflict as err:
                _LOGGER.error("%s", err)
                self._record("conflict", reason, str(err))
            except GitError as err:
                _LOGGER.error("Sync failed: %s", err)
                self._record("error", reason, str(err))
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during sync")
                self._record("error", reason, str(err))
            return self._state

    def _do_sync(self) -> bool:
        """Run the configured strategy. Returns True if local files changed."""
        opts = self.options
        repo = self.repo
        strategy = opts.sync_strategy
        changed = False

        fetched = True
        try:
            repo.fetch()
        except GitError as err:
            fetched = False
            _LOGGER.warning("Fetch failed (continuing): %s", err)

        remote_has = repo.remote_branch_exists(opts.branch) if fetched else False

        # Stage and commit local changes (unless the remote is authoritative).
        if strategy != "remote_wins":
            self._write_gitignore()
            repo.add_all()
            if repo.has_staged_changes():
                repo.commit(self._commit_message())
                _LOGGER.info("Committed local configuration changes")

        if strategy == "remote_wins":
            if remote_has:
                behind, ahead = repo.ahead_behind(opts.branch)
                if behind > 0 or ahead > 0 or repo.is_dirty():
                    _LOGGER.info("Resetting local config to origin/%s", opts.branch)
                    repo.reset_hard(opts.branch)
                    if opts.restore_clean:
                        repo.clean(opts.excludes)
                    changed = behind > 0 or repo.is_dirty()
            else:
                self._seed_remote()

        elif strategy == "local_wins":
            if repo.has_commits():
                behind, _ = repo.ahead_behind(opts.branch)
                repo.push(opts.branch, force=behind > 0)
                _LOGGER.info("Pushed local configuration to origin/%s", opts.branch)

        else:  # rebase — true two-way sync
            if remote_has:
                behind, ahead = repo.ahead_behind(opts.branch)
                if behind > 0:
                    _LOGGER.info("Rebasing local commits onto origin/%s", opts.branch)
                    repo.rebase_onto(opts.branch)
                    changed = True
                _, ahead = repo.ahead_behind(opts.branch)
                if ahead > 0:
                    repo.push(opts.branch)
                    _LOGGER.info("Pushed configuration to origin/%s", opts.branch)
            else:
                self._seed_remote()

        return changed

    def _seed_remote(self) -> None:
        """Create the remote branch from the local configuration."""
        repo = self.repo
        self._write_gitignore()
        repo.add_all()
        if repo.has_staged_changes():
            repo.commit(self._commit_message())
        if repo.has_commits():
            repo.push(self.options.branch)
            _LOGGER.info("Seeded origin/%s from local configuration", self.options.branch)

    # ---------------------------------------------------------------- restore
    def restore(self, reason: str = "manual") -> dict[str, Any]:
        """Force the local configuration to match the remote branch."""
        if not self.options.configured:
            self._record("idle", reason, "No repository URL configured")
            return self._state
        with self._lock:
            try:
                self.ensure_setup()
                opts = self.options
                self.repo.fetch()
                if not self.repo.remote_branch_exists(opts.branch):
                    msg = f"Remote branch '{opts.branch}' does not exist yet"
                    _LOGGER.warning(msg)
                    self._record("idle", reason, msg)
                    return self._state
                _LOGGER.info("Restoring configuration from origin/%s", opts.branch)
                self.repo.reset_hard(opts.branch)
                if opts.restore_clean:
                    self.repo.clean(opts.excludes)
                applied = self._maybe_apply()
                self._record("ok", reason, f"Restored from {opts.branch}", applied)
            except GitError as err:
                _LOGGER.error("Restore failed: %s", err)
                self._record("error", reason, str(err))
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during restore")
                self._record("error", reason, str(err))
            return self._state

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        opts = self.options
        git_state: dict[str, Any] = {"initialized": False}
        with self._lock:
            try:
                if opts.configured and self.repo.initialized:
                    behind, ahead = (0, 0)
                    try:
                        behind, ahead = self.repo.ahead_behind(opts.branch)
                    except GitError:
                        pass
                    head = self.repo.head_info()
                    git_state = {
                        "initialized": True,
                        "branch": self.repo.current_branch(),
                        "dirty": self.repo.is_dirty(),
                        "ahead": ahead,
                        "behind": behind,
                        "head": (
                            {"short": head.short, "subject": head.subject, "date": head.date}
                            if head
                            else None
                        ),
                    }
            except GitError as err:
                git_state = {"initialized": False, "error": str(err)}

        return {
            "configured": opts.configured,
            "enabled": opts.enabled,
            "repository": opts.safe_repository_url,
            "branch": opts.branch,
            "strategy": opts.sync_strategy,
            "auth_method": opts.auth_method,
            "apply_action": opts.apply_action,
            "config_path": opts.config_path,
            "automation": {
                "interval_minutes": opts.sync_interval_minutes,
                "watch_changes": opts.watch_changes,
                "watch_running": bool(self._watcher_running_getter()),
                "debounce_seconds": opts.watch_debounce_seconds,
            },
            "git": git_state,
            "last": {
                "result": self._state.get("last_result", "never"),
                "reason": self._state.get("last_reason", ""),
                "message": self._state.get("last_message", ""),
                "run": self._state.get("last_run", ""),
                "applied": self._state.get("last_applied", ""),
            },
        }
