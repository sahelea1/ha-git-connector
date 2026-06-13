"""A small, purpose-built wrapper around the ``git`` command line.

Design goals:

* **Never persist secrets.** For HTTPS/token auth the credentials are fed to
  git through an inline credential helper that reads them from the process
  environment, so the access token is never written to ``.git/config``. For SSH
  auth the private key lives only in ``/data`` (outside the backed-up config).
* **Be safe inside a container.** ``safe.directory`` is set so git does not
  refuse to operate on a bind-mounted directory owned by another user, and
  ``GIT_TERMINAL_PROMPT=0`` makes authentication failures fail fast instead of
  hanging on a prompt.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from dataclasses import dataclass

_LOGGER = logging.getLogger("gitsync.git")

REMOTE = "origin"

# Inline credential helper: emits the username/password from the environment.
_CRED_HELPER = (
    r'!f() { printf "username=%s\npassword=%s\n" "$GIT_USERNAME" "$GIT_PASSWORD"; }; f'
)


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


class SyncConflict(GitError):
    """Raised when local and remote history diverge and cannot be reconciled."""


@dataclass
class HeadInfo:
    short: str
    subject: str
    date: str


class GitRepo:
    def __init__(
        self,
        path: str,
        *,
        auth_method: str,
        url: str,
        username: str,
        token: str,
        ssh_key: str,
        verify_ssl: bool,
        user_name: str,
        user_email: str,
        data_dir: str,
    ) -> None:
        self.path = path
        self.auth_method = auth_method
        self.url = url.strip()
        self.username = username
        self.token = token
        self.ssh_key = ssh_key
        self.verify_ssl = verify_ssl
        self.user_name = user_name
        self.user_email = user_email
        self.ssh_dir = os.path.join(data_dir, "ssh")

    # ============================================================ environment
    def _global_args(self) -> list[str]:
        args = [
            "-c", "safe.directory=*",
            "-c", "core.fileMode=false",
            "-c", "advice.detachedHead=false",
        ]
        if self.auth_method == "token" and self.token:
            # Disable any inherited helper, then install our ephemeral one.
            args += ["-c", "credential.helper="]
            args += ["-c", f"credential.helper={_CRED_HELPER}"]
        if not self.verify_ssl:
            args += ["-c", "http.sslVerify=false"]
        return args

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        if self.auth_method == "token" and self.token:
            env["GIT_USERNAME"] = self.username
            env["GIT_PASSWORD"] = self.token
        elif self.auth_method == "ssh":
            env["GIT_SSH_COMMAND"] = self._ssh_command()
        return env

    def _ssh_command(self) -> str:
        key_path = os.path.join(self.ssh_dir, "id_key")
        known_hosts = os.path.join(self.ssh_dir, "known_hosts")
        return (
            f"ssh -i {key_path}"
            " -o IdentitiesOnly=yes"
            " -o BatchMode=yes"
            " -o StrictHostKeyChecking=accept-new"
            f" -o UserKnownHostsFile={known_hosts}"
        )

    def prepare_ssh_key(self) -> None:
        """Materialise the SSH private key on disk with strict permissions."""
        if self.auth_method != "ssh" or not self.ssh_key.strip():
            return
        os.makedirs(self.ssh_dir, mode=0o700, exist_ok=True)
        os.chmod(self.ssh_dir, 0o700)
        key_path = os.path.join(self.ssh_dir, "id_key")
        key = self.ssh_key.replace("\r\n", "\n").strip() + "\n"
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write(key)
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        known_hosts = os.path.join(self.ssh_dir, "known_hosts")
        if not os.path.exists(known_hosts):
            open(known_hosts, "a", encoding="utf-8").close()
            os.chmod(known_hosts, stat.S_IRUSR | stat.S_IWUSR)

    # ================================================================= runner
    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", *self._global_args(), *args]
        proc = subprocess.run(
            cmd,
            cwd=self.path,
            env=self._env(),
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip()
            raise GitError(f"git {args[0]} failed: {message}")
        return proc

    # =============================================================== plumbing
    @property
    def initialized(self) -> bool:
        return os.path.isdir(os.path.join(self.path, ".git"))

    def init(self, branch: str) -> None:
        self._run("init", "-b", branch)

    def set_identity(self) -> None:
        self._run("config", "user.name", self.user_name)
        self._run("config", "user.email", self.user_email)

    def set_remote(self) -> None:
        existing = self._run("remote", check=False).stdout.split()
        if REMOTE in existing:
            self._run("remote", "set-url", REMOTE, self.url)
        else:
            self._run("remote", "add", REMOTE, self.url)

    def current_branch(self) -> str | None:
        proc = self._run("symbolic-ref", "--short", "-q", "HEAD", check=False)
        name = proc.stdout.strip()
        return name or None

    def has_commits(self) -> bool:
        return self._run("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def fetch(self) -> None:
        self._run("fetch", "--prune", REMOTE)

    def remote_branch_exists(self, branch: str) -> bool:
        """Check for ``origin/<branch>`` in the local fetched refs."""
        ref = f"refs/remotes/{REMOTE}/{branch}"
        return self._run("rev-parse", "--verify", "-q", ref, check=False).returncode == 0

    def ls_remote_has(self, branch: str) -> bool:
        """Ask the remote directly whether a branch exists (network call)."""
        proc = self._run("ls-remote", "--heads", REMOTE, branch, check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def list_remote_branches(self) -> list[str]:
        """Return the branch names that exist on ``origin``.

        Uses a direct ``ls-remote`` network call. Returns an empty list if the
        remote is unreachable so callers never have to handle an exception.
        """
        try:
            proc = self._run("ls-remote", "--heads", REMOTE, check=False)
        except GitError:
            return []
        if proc.returncode != 0:
            return []
        branches: list[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            ref = line.split("\t", 1)[1].strip()
            if ref.startswith("refs/heads/"):
                branches.append(ref[len("refs/heads/"):])
        return branches

    def checkout_force(self, branch: str, start_point: str | None = None) -> None:
        if start_point:
            self._run("checkout", "-f", "-B", branch, start_point)
        else:
            self._run("checkout", "-f", "-B", branch)

    def set_upstream(self, branch: str) -> None:
        self._run(
            "branch", f"--set-upstream-to={REMOTE}/{branch}", branch, check=False
        )

    def add_all(self) -> None:
        self._run("add", "-A")

    def has_staged_changes(self) -> bool:
        return self._run("diff", "--cached", "--quiet", check=False).returncode != 0

    def is_dirty(self) -> bool:
        return bool(self._run("status", "--porcelain", check=False).stdout.strip())

    def commit(self, message: str) -> None:
        self._run("commit", "-m", message)

    def ahead_behind(self, branch: str) -> tuple[int, int]:
        """Return ``(behind, ahead)`` relative to ``origin/<branch>``."""
        if not self.has_commits() or not self.remote_branch_exists(branch):
            return (0, 0)
        proc = self._run(
            "rev-list",
            "--left-right",
            "--count",
            f"{REMOTE}/{branch}...HEAD",
            check=False,
        )
        try:
            behind, ahead = proc.stdout.split()
            return (int(behind), int(ahead))
        except (ValueError, IndexError):
            return (0, 0)

    def rebase_onto(self, branch: str) -> None:
        proc = self._run("rebase", f"{REMOTE}/{branch}", check=False)
        if proc.returncode != 0:
            self._run("rebase", "--abort", check=False)
            raise SyncConflict(
                "Local and remote histories conflict and could not be "
                "rebased automatically. Use 'Restore' to take the remote "
                "version, or resolve the conflict manually."
            )

    def reset_hard(self, branch: str) -> None:
        self._run("reset", "--hard", f"{REMOTE}/{branch}")

    def clean(self, extra_excludes: list[str]) -> None:
        """Remove untracked files, protecting runtime/sensitive paths.

        ``-x`` is intentionally never used, so files ignored by ``.gitignore``
        (database, ``.storage`` …) are always preserved. The explicit excludes
        below add a second safety net independent of the ignore file.
        """
        protected = [
            ".git", ".storage", ".cloud", "*.db*", "*.log*",
            "backups", "tts", "deps", "__pycache__", ".gitignore",
            "secrets.yaml",
        ]
        args = ["clean", "-fd"]
        for pattern in protected + list(extra_excludes):
            args += ["-e", pattern]
        self._run(*args)

    def push(self, branch: str, *, force: bool = False) -> None:
        args = ["push"]
        if force:
            args.append("--force-with-lease")
        args += ["-u", REMOTE, branch]
        self._run(*args)

    def promote(self, source_local_branch: str, target_remote_branch: str) -> None:
        """Publish ``source_local_branch``'s current commit onto a remote branch.

        Pushes ``<source>:<target>`` to origin so ``origin/<target>`` ends up
        pointing at the same commit as the local source branch. The push is
        forced so a fast-forward is not required, but ``--force-with-lease`` is
        used when the target already exists so we never clobber commits that the
        local repository has not yet seen.
        """
        spec = f"{source_local_branch}:{target_remote_branch}"
        if self.remote_branch_exists(target_remote_branch):
            self._run("push", "--force-with-lease", REMOTE, spec)
        else:
            self._run("push", REMOTE, spec)

    def head_info(self) -> HeadInfo | None:
        if not self.has_commits():
            return None
        proc = self._run(
            "log", "-1", "--no-color", "--pretty=%h%x1f%s%x1f%cI", check=False
        )
        parts = proc.stdout.strip().split("\x1f")
        if len(parts) != 3:
            return None
        return HeadInfo(short=parts[0], subject=parts[1], date=parts[2])

    def list_commits(self, branch: str, limit: int = 30) -> list[dict[str, str]]:
        """Return recent commits on ``origin/<branch>``."""
        ref = f"{REMOTE}/{branch}"
        if not self.remote_branch_exists(branch):
            return []
        proc = self._run(
            "log", ref, f"-{limit}", "--no-color",
            "--pretty=%H%x1f%h%x1f%s%x1f%cI",
            check=False,
        )
        if proc.returncode != 0:
            return []
        commits: list[dict[str, str]] = []
        for line in proc.stdout.strip().splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "short": parts[1],
                    "subject": parts[2],
                    "date": parts[3],
                })
        return commits

    def reset_hard_commit(self, commit: str) -> None:
        """Reset the working tree to a specific commit hash."""
        self._run("reset", "--hard", commit)
