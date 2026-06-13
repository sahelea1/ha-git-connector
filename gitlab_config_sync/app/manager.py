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

        # The branch Home Assistant is currently running. Defaults to the
        # configured prod/source branch on first run or when unset, preserving
        # the historical single-branch behaviour for existing users.
        active = self._state.get("active_branch")
        if not active:
            active = options.branch
            self._state["active_branch"] = active
            self._save_state()
        self.active_branch: str = active

        # Best-effort cache of the remote branch list for /api/status so a slow
        # or unreachable remote never blocks the dashboard.
        self._branches_cache: list[str] = []

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

    def _set_active_branch(self, branch: str) -> None:
        self.active_branch = branch
        self._state["active_branch"] = branch
        self._save_state()

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
        branch = self.active_branch
        self.repo.prepare_ssh_key()

        first_run = not self.repo.initialized
        if first_run:
            _LOGGER.info("Initialising git repository in %s", opts.config_path)
            self.repo.init(branch)

        self.repo.set_identity()
        self.repo.set_remote()
        self._write_gitignore()

        initial_restore = False
        if first_run:
            # Try to adopt an existing remote branch so a freshly installed
            # Home Assistant picks up the known-good configuration.
            try:
                self.repo.fetch()
                if self.repo.remote_branch_exists(branch):
                    _LOGGER.info(
                        "Existing '%s' branch found on remote — adopting it", branch
                    )
                    self.repo.checkout_force(
                        branch, f"origin/{branch}"
                    )
                    self.repo.set_upstream(branch)
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
        """Make sure the active branch is checked out for an existing repo."""
        branch = self.active_branch
        current = self.repo.current_branch()
        if current == branch:
            return
        try:
            self.repo.fetch()
        except GitError:
            pass
        try:
            if self.repo.remote_branch_exists(branch):
                self.repo.checkout_force(branch, f"origin/{branch}")
            else:
                self.repo.checkout_force(branch)
            self.repo.set_upstream(branch)
            _LOGGER.info("Switched to branch '%s'", branch)
        except GitError as err:
            _LOGGER.error("Could not switch to branch '%s': %s", branch, err)

    # ------------------------------------------------------------------- apply
    def _maybe_apply(self, override: str | None = None) -> str:
        """Optionally validate and reload/restart Home Assistant after a change.

        ``override`` lets callers (e.g. branch switch/promote from the UI) force
        a specific action; when omitted the configured ``apply_action`` is used.
        """
        action = override if override is not None else self.options.apply_action
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
    def sync(self, reason: str = "manual", message: str = "") -> dict[str, Any]:
        """Two-way (or strategy-specific) synchronisation."""
        if not self.options.configured:
            self._record("idle", reason, "No repository URL configured")
            return self._state
        with self._lock:
            try:
                applied_local = self.ensure_setup()
                changed = self._do_sync(message=message)
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

    def _do_sync(self, message: str = "") -> bool:
        """Run the configured strategy. Returns True if local files changed."""
        opts = self.options
        repo = self.repo
        branch = self.active_branch
        strategy = opts.sync_strategy
        changed = False
        commit_msg = message or self._commit_message()

        fetched = True
        try:
            repo.fetch()
        except GitError as err:
            fetched = False
            _LOGGER.warning("Fetch failed (continuing): %s", err)

        remote_has = repo.remote_branch_exists(branch) if fetched else False

        # Stage and commit local changes (unless the remote is authoritative).
        if strategy != "remote_wins":
            self._write_gitignore()
            repo.add_all()
            if repo.has_staged_changes():
                repo.commit(commit_msg)
                _LOGGER.info("Committed local configuration changes")

        if strategy == "remote_wins":
            if remote_has:
                behind, ahead = repo.ahead_behind(branch)
                if behind > 0 or ahead > 0 or repo.is_dirty():
                    _LOGGER.info("Resetting local config to origin/%s", branch)
                    repo.reset_hard(branch)
                    if opts.restore_clean:
                        repo.clean(opts.excludes)
                    changed = behind > 0 or repo.is_dirty()
            else:
                self._seed_remote()

        elif strategy == "local_wins":
            if repo.has_commits():
                behind, _ = repo.ahead_behind(branch)
                repo.push(branch, force=behind > 0)
                _LOGGER.info("Pushed local configuration to origin/%s", branch)

        else:  # rebase — true two-way sync
            if remote_has:
                behind, ahead = repo.ahead_behind(branch)
                if behind > 0:
                    _LOGGER.info("Rebasing local commits onto origin/%s", branch)
                    repo.rebase_onto(branch)
                    changed = True
                _, ahead = repo.ahead_behind(branch)
                if ahead > 0:
                    repo.push(branch)
                    _LOGGER.info("Pushed configuration to origin/%s", branch)
            else:
                self._seed_remote()

        return changed

    def _seed_remote(self) -> None:
        """Create the remote branch from the local configuration."""
        repo = self.repo
        branch = self.active_branch
        self._write_gitignore()
        repo.add_all()
        if repo.has_staged_changes():
            repo.commit(self._commit_message())
        if repo.has_commits():
            repo.push(branch)
            _LOGGER.info("Seeded origin/%s from local configuration", branch)

    # ---------------------------------------------------------------- restore
    def restore(
        self, reason: str = "manual", branch: str = "", commit: str = ""
    ) -> dict[str, Any]:
        """Force the local configuration to match a remote branch or commit."""
        if not self.options.configured:
            self._record("idle", reason, "No repository URL configured")
            return self._state
        with self._lock:
            try:
                self.ensure_setup()
                opts = self.options
                target = branch or self.active_branch
                self.repo.fetch()
                if not self.repo.remote_branch_exists(target):
                    msg = f"Remote branch '{target}' does not exist yet"
                    _LOGGER.warning(msg)
                    self._record("idle", reason, msg)
                    return self._state
                if commit:
                    _LOGGER.info(
                        "Restoring configuration to commit %s on %s", commit[:8], target
                    )
                    self.repo.reset_hard_commit(commit)
                else:
                    _LOGGER.info("Restoring configuration from origin/%s", target)
                    self.repo.reset_hard(target)
                if opts.restore_clean:
                    self.repo.clean(opts.excludes)
                label = f"{target}@{commit[:8]}" if commit else target
                applied = self._maybe_apply()
                self._record("ok", reason, f"Restored from {label}", applied)
            except GitError as err:
                _LOGGER.error("Restore failed: %s", err)
                self._record("error", reason, str(err))
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during restore")
                self._record("error", reason, str(err))
            return self._state

    def list_commits(self, branch: str = "") -> list[dict[str, str]]:
        """Return recent commits for a branch (fetches first)."""
        target = branch or self.active_branch
        if not self.options.configured or not self.repo.initialized:
            return []
        with self._lock:
            try:
                self.repo.fetch()
            except GitError:
                pass
            return self.repo.list_commits(target)

    def push_to_branch(
        self, target_branch: str, reason: str = "ui"
    ) -> dict[str, Any]:
        """Commit local changes and push them to an arbitrary remote branch."""
        if not self.options.configured:
            return {"ok": False, "message": "No repository URL configured"}
        target_branch = (target_branch or "").strip()
        if not target_branch:
            return {"ok": False, "message": "No target branch specified"}
        with self._lock:
            try:
                self.ensure_setup()
                repo = self.repo
                self._write_gitignore()
                repo.add_all()
                if repo.has_staged_changes():
                    repo.commit(self._commit_message())
                if not repo.has_commits():
                    return {"ok": False, "message": "No commits to push"}
                try:
                    repo.fetch()
                except GitError as err:
                    _LOGGER.warning("Fetch failed before push (continuing): %s", err)
                source = self.active_branch
                if source == target_branch:
                    behind, _ = repo.ahead_behind(target_branch)
                    repo.push(target_branch, force=behind > 0)
                else:
                    repo.promote(source, target_branch)
                msg = f"Pushed to '{target_branch}'"
                _LOGGER.info(msg)
                self._record("ok", reason, msg)
                return {"ok": True, "message": msg}
            except GitError as err:
                _LOGGER.error("Push to '%s' failed: %s", target_branch, err)
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during push")
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}

    def create_branch(
        self, name: str, from_branch: str = "", reason: str = "ui"
    ) -> dict[str, Any]:
        """Create a new feature branch and switch to it."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "No branch name specified"}
        if not self.options.configured:
            return {"ok": False, "message": "No repository URL configured"}
        with self._lock:
            try:
                self.ensure_setup()
                repo = self.repo
                self._write_gitignore()
                repo.add_all()
                if repo.has_staged_changes():
                    repo.commit(self._commit_message())
                try:
                    repo.fetch()
                except GitError as err:
                    _LOGGER.warning(
                        "Fetch failed before branch creation (continuing): %s", err
                    )
                source = from_branch or self.active_branch
                if repo.remote_branch_exists(source):
                    start = f"origin/{source}"
                else:
                    start = None
                repo.checkout_force(name, start)
                repo.set_upstream(name)
                self._set_active_branch(name)
                if repo.has_commits():
                    repo.push(name)
                msg = f"Created branch '{name}' from '{source}'"
                _LOGGER.info(msg)
                self._record("ok", reason, msg)
                return {"ok": True, "message": msg}
            except GitError as err:
                _LOGGER.error("Branch creation failed: %s", err)
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during branch creation")
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}

    # ---------------------------------------------------------------- branches
    def list_branches(self) -> list[str]:
        """Return the known branches: remote heads plus the configured
        prod/dev branches and the current active branch, de-duplicated.

        Best-effort — a slow or unreachable remote yields just the local names.
        """
        opts = self.options
        names: list[str] = []
        try:
            if opts.configured and self.repo.initialized:
                remote = self.repo.list_remote_branches()
            else:
                remote = []
        except GitError:
            remote = []
        for candidate in [*remote, opts.prod_branch, opts.dev_branch, self.active_branch]:
            if candidate and candidate not in names:
                names.append(candidate)
        self._branches_cache = names
        return names

    def switch_branch(
        self, branch: str, apply: str = "none", reason: str = "ui"
    ) -> dict[str, Any]:
        """Switch the active environment to ``branch``.

        Local changes on the current branch are committed first (so nothing is
        lost), the target branch is fetched/checked out (created from the
        current state when it does not exist on the remote yet), the working
        configuration is synchronised to it and ``apply`` (none|reload|restart)
        is optionally run.
        """
        branch = (branch or "").strip()
        if not branch:
            return {"ok": False, "message": "No branch specified"}
        if not self.options.allow_branch_switch:
            return {"ok": False, "message": "Branch switching is disabled"}
        if not self.options.configured:
            return {"ok": False, "message": "No repository URL configured"}

        with self._lock:
            try:
                self.ensure_setup()
                repo = self.repo

                if branch == self.active_branch:
                    return {"ok": True, "message": f"Already on '{branch}'"}

                # Preserve any in-flight local edits on the current branch.
                self._write_gitignore()
                repo.add_all()
                if repo.has_staged_changes():
                    repo.commit(self._commit_message())
                    _LOGGER.info("Committed local changes before switching branch")

                try:
                    repo.fetch()
                except GitError as err:
                    _LOGGER.warning("Fetch failed before switch (continuing): %s", err)

                if repo.remote_branch_exists(branch):
                    repo.checkout_force(branch, f"origin/{branch}")
                else:
                    # Create the new branch from the current configuration.
                    repo.checkout_force(branch)
                repo.set_upstream(branch)
                self._set_active_branch(branch)
                _LOGGER.info("Active environment switched to '%s'", branch)

                # Bring the working tree / remote in line with the new branch.
                self._do_sync()

                applied = self._maybe_apply(override=apply)
                msg = f"Switched to '{branch}'"
                self._record("ok", reason, msg, applied)
                return {"ok": True, "message": msg, "applied": applied}
            except SyncConflict as err:
                _LOGGER.error("%s", err)
                self._record("conflict", reason, str(err))
                return {"ok": False, "message": str(err)}
            except GitError as err:
                _LOGGER.error("Branch switch failed: %s", err)
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during branch switch")
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}

    def promote(self, apply: str = "none", reason: str = "ui") -> dict[str, Any]:
        """Promote the current active branch onto the prod branch on origin.

        Intended to publish a tested ``dev`` branch to ``prod``. If the active
        branch is already prod this is a no-op. The active branch is left
        unchanged so the user keeps working where they were.
        """
        if not self.options.configured:
            return {"ok": False, "message": "No repository URL configured"}

        target = self.options.prod_branch
        with self._lock:
            try:
                if self.active_branch == target:
                    msg = f"Active branch is already '{target}'; nothing to promote"
                    self._record("idle", reason, msg)
                    return {"ok": True, "message": msg}

                self.ensure_setup()
                repo = self.repo

                # Make sure the active branch's local commits are pushed first.
                self._write_gitignore()
                repo.add_all()
                if repo.has_staged_changes():
                    repo.commit(self._commit_message())

                try:
                    repo.fetch()
                except GitError as err:
                    _LOGGER.warning("Fetch failed before promote (continuing): %s", err)

                source = self.active_branch
                if repo.has_commits():
                    repo.push(source)
                repo.promote(source, target)
                msg = f"Promoted '{source}' to '{target}'"
                _LOGGER.info(msg)
                applied = self._maybe_apply(override=apply)
                self._record("ok", reason, msg, applied)
                return {"ok": True, "message": msg, "applied": applied}
            except GitError as err:
                _LOGGER.error("Promote failed: %s", err)
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error during promote")
                self._record("error", reason, str(err))
                return {"ok": False, "message": str(err)}

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        opts = self.options
        git_state: dict[str, Any] = {"initialized": False}
        with self._lock:
            try:
                if opts.configured and self.repo.initialized:
                    behind, ahead = (0, 0)
                    try:
                        behind, ahead = self.repo.ahead_behind(self.active_branch)
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

        # Best-effort branch list. Never let a slow/failing remote call break
        # /api/status — fall back to the last cached value on error.
        try:
            branches = self.list_branches()
        except Exception:  # pragma: no cover - defensive
            branches = self._branches_cache

        active = self.active_branch
        return {
            "configured": opts.configured,
            "enabled": opts.enabled,
            "repository": opts.safe_repository_url,
            "branch": opts.branch,
            "active_branch": active,
            "prod_branch": opts.prod_branch,
            "dev_branch": opts.dev_branch,
            "is_dev": active != opts.prod_branch,
            "allow_branch_switch": opts.allow_branch_switch,
            "branches": branches,
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
