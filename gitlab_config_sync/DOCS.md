# Riti Git Sync — Documentation

This add-on version-controls your Home Assistant configuration with Git and a
private GitLab repository. It backs up your `/config` directory automatically
and lets you restore it from a branch when something goes wrong.

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Authentication](#authentication)
  - [Token (HTTPS) — recommended](#token-https--recommended)
  - [SSH key](#ssh-key)
- [Configuration options](#configuration-options)
- [Sync strategies](#sync-strategies)
- [DEV / PROD environments](#dev--prod-environments)
- [The recovery workflow](#the-recovery-workflow)
- [What is and isn't backed up](#what-is-and-isnt-backed-up)
- [Applying changes (reload / restart)](#applying-changes-reload--restart)
- [The dashboard](#the-dashboard)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

## How it works

The add-on turns your Home Assistant configuration directory into a Git working
tree (a `.git` folder is created inside `/config`). It then keeps that tree and a
branch on your GitLab server in sync:

```
        commit + push (on change / on a timer)
 ┌────────────────────────────────────────────────►┐
 │                                                  │
Home Assistant /config                       GitLab repo
 (configuration.yaml,                         branch: prod
  automations.yaml, …)                              │
 │                                                  │
 └◄────────────────────────────────────────────────┘
              reset / pull  (Restore)
```

- **Backup** happens automatically: on a timer (`sync_interval_minutes`) and a
  short moment after you change a file (`watch_changes`).
- **Restore** brings the branch back down into Home Assistant. You can trigger
  it manually from the dashboard, on start (`restore_on_start`), or continuously
  with the `remote_wins` strategy.

## Prerequisites

- **Home Assistant OS** or **Supervised** (the add-on system is required).
- Access to a **GitLab** instance (gitlab.com or self-hosted) and permission to
  create a repository and an access token / deploy key.
- An **empty** (or dedicated) repository for your configuration, e.g.
  `ha-config`. The add-on creates the configured branch automatically on first
  run.

## Installation

1. Add this repository to the Add-on Store (see the repository
   [README](../README.md)).
2. Install **Riti Git Sync**.
3. Open the **Configuration** tab and fill in at least `repository_url` and your
   credentials (see below).
4. **Start** the add-on and open the **Web UI** to confirm everything is green.

## Authentication

### Token (HTTPS) — recommended

1. In GitLab, open your configuration repository → **Settings → Access Tokens**
   (a *Project Access Token*), or your user **Preferences → Access Tokens** (a
   *Personal Access Token*).
2. Create a token with the **`write_repository`** scope (role `Developer` or
   higher for project tokens).
3. In the add-on configuration set:

   ```yaml
   repository_url: "https://gitlab.example.com/you/ha-config.git"
   auth_method: token
   username: ""          # leave empty → "oauth2" is used, which works with tokens
   token: "glpat-xxxxxxxxxxxxxxxxxxxx"
   ```

The token is stored in the add-on's protected options and is fed to Git
in-memory only — it is **never written** into `.git/config` or committed.

### SSH key

1. Generate a key pair (no passphrase), for example:

   ```bash
   ssh-keygen -t ed25519 -C "ha-config-sync" -f ha_deploy_key -N ""
   ```

2. In GitLab, add `ha_deploy_key.pub` as a **Deploy Key** on the repository with
   **write access** (Settings → Repository → Deploy keys).
3. Paste the **private** key into the add-on configuration:

   ```yaml
   repository_url: "git@gitlab.example.com:you/ha-config.git"
   auth_method: ssh
   ssh_key: |
     -----BEGIN OPENSSH PRIVATE KEY-----
     ...
     -----END OPENSSH PRIVATE KEY-----
   ```

The private key is written only to the add-on's private `/data` directory with
`0600` permissions. The host key is trusted on first use
(`StrictHostKeyChecking=accept-new`).

## Configuration options

| Option | Default | Description |
| --- | --- | --- |
| `repository_url` | — | GitLab repo URL. HTTPS for token auth, SSH URL for SSH auth. |
| `branch` | `prod` | The stable PROD branch — source of truth for backups and restores. |
| `dev_branch` | `dev` | The DEV sandbox branch you can switch to and test on before promoting to PROD. |
| `allow_branch_switch` | `true` | Allow switching the active environment (branch) and promoting DEV → PROD from the dashboard. |
| `auth_method` | `token` | `token` (HTTPS access token) or `ssh` (deploy key). |
| `username` | `""` | Username for token auth. Empty → `oauth2` (works with GitLab tokens). |
| `token` | `""` | GitLab access token with `write_repository` scope. |
| `ssh_key` | `""` | SSH private key (only for `auth_method: ssh`). |
| `commit_name` | `Home Assistant` | Author name for backup commits. |
| `commit_email` | `homeassistant@local` | Author email for backup commits. |
| `commit_message` | `Home Assistant config backup` | Prefix for commit messages (a timestamp is appended). |
| `enabled` | `true` | Master switch for automatic syncing. The UI still works when off. |
| `sync_strategy` | `rebase` | `rebase`, `remote_wins`, or `local_wins` — see below. |
| `sync_interval_minutes` | `60` | How often to sync automatically. `0` disables the timer. |
| `watch_changes` | `true` | Sync shortly after configuration files change. |
| `watch_debounce_seconds` | `30` | Quiet period after the last change before syncing. |
| `restore_on_start` | `false` | Force a restore from the branch each time the add-on starts. |
| `restore_clean` | `false` | On restore, also delete untracked files (sensitive files are protected). |
| `apply_action` | `none` | After remote changes: `none`, `reload` (YAML), or `restart` (Core). |
| `check_config_before_apply` | `true` | Validate the config before a reload/restart. |
| `verify_ssl` | `true` | Verify the GitLab TLS certificate. Disable only for self-signed certs. |
| `excludes` | `[]` | Extra `.gitignore` patterns (one per line) to never back up. |
| `log_level` | `info` | `trace`, `debug`, `info`, `notice`, `warning`, `error`, `fatal`. |

## Sync strategies

Choose the behaviour that matches who is the "source of truth":

- **`rebase` (two-way, default).** Local edits are committed and pushed; remote
  edits are pulled in. Best for everyday use. If the *same* lines change on both
  sides at once it cannot auto-merge — it reports a conflict and leaves your
  files untouched. Use **Restore** to take the remote version.
- **`remote_wins` (mirror down).** GitLab is authoritative. On every sync the
  local configuration is reset to match the branch; local edits to tracked files
  are discarded. Ideal for "I manage everything in GitLab and Home Assistant
  just follows."
- **`local_wins` (backup only).** Home Assistant is authoritative. Local state
  is force-pushed to the branch. Use this for pure off-site backups.

## DEV / PROD environments

Riti Git Sync treats two branches as named environments so you can test changes
safely before they go live:

- **PROD** — the `branch` option (default `prod`). This is your stable source of
  truth: the configuration Home Assistant runs in production.
- **DEV** — the `dev_branch` option (default `dev`). A sandbox branch where you
  can iterate on changes without touching prod.

### Switching the active environment

The **active environment** is the branch Home Assistant currently runs from.
When `allow_branch_switch` is `true` (the default), the dashboard exposes a
control to switch between DEV and PROD:

1. Press **Switch to DEV** on the dashboard. The add-on checks out `dev_branch`
   (creating it from prod on first use) and makes it the active environment.
2. Edit and test your configuration. Every change is committed and pushed to
   the DEV branch, exactly as normal syncing works — your prod branch is left
   untouched.
3. When you are happy with the result, press **Switch to PROD** to go back, or
   promote your work (below).

### Promoting DEV → PROD

Once DEV is tested, press **Promote DEV → PROD** on the dashboard. This publishes
the tested DEV configuration onto the `prod` branch, making it the new source of
truth. After promoting you typically switch the active environment back to PROD.

### Applying changes on switch / promote

Switching the active environment or promoting changes which files are on disk,
so the configured `apply_action` is honoured just like a restore:

- `none` — files are changed on disk; you reload/restart yourself.
- `reload` — call `homeassistant.reload_all` after the switch/promote.
- `restart` — restart Home Assistant Core.

When `check_config_before_apply` is on, the configuration is validated first and
the reload/restart is skipped if it is invalid.

> Set `allow_branch_switch: false` if you want to lock the dashboard to a single
> environment and prevent accidental switches or promotions.

## The recovery workflow

This is the scenario the add-on is built for.

1. A bad edit (or a failed update) leaves Home Assistant broken.
2. Open your repository in GitLab and switch to the `prod` branch.
3. Fix the offending file(s) directly in GitLab's web editor and commit to
   `prod`.
4. Bring the fix back to Home Assistant, either:
   - press **Restore from "prod"** in the add-on's Web UI; **or**
   - set `restore_on_start: true` and restart the add-on; **or**
   - run with `sync_strategy: remote_wins` so it happens on the next sync.
5. If you set `apply_action` to `reload` or `restart`, Home Assistant applies the
   restored configuration automatically (after an optional validity check).

Because the database, `.storage` and secrets live outside Git (see below), a
restore changes only your YAML/config files — it will not roll back your history
or entity states.

## What is and isn't backed up

A managed block is maintained in `/config/.gitignore`. By default the add-on
**backs up** your human-edited configuration and **excludes** large, volatile or
sensitive runtime state:

**Excluded by default**

- `home-assistant_v2.db*` and any `*.db*` (the recorder database)
- `*.log` log files
- `.storage/` — **auth tokens, users, and UI/integration state** (sensitive)
- `.cloud/`, `.uuid`, `ip_bans.yaml`, `.HA_VERSION`
- `backups/`, `*.tar`, dependency/cache folders (`deps/`, `tts/`, `__pycache__/`)

**Included by default**

- `configuration.yaml`, `automations.yaml`, `scripts.yaml`, `scenes.yaml`
- `secrets.yaml` (it lives in your **private** repo and is needed for a working
  restore — add it to `excludes` if you would rather keep it out)
- Packages, custom dashboards stored as YAML, `custom_components/`, themes, etc.

> **Note:** Because `.storage/` is excluded, configuration you created through
> the UI (most integrations, helpers, dashboards stored in storage, areas, …) is
> **not** captured. This add-on version-controls your *YAML* configuration. If
> you need those too, add the relevant paths — but be aware `.storage` contains
> credentials and tokens.

Add your own patterns with the `excludes` option; remove protections by editing
the non-managed part of `/config/.gitignore`.

## Applying changes (reload / restart)

After a restore (or a pull that changes files), the add-on can apply the new
configuration for you via `apply_action`:

- `none` — do nothing (you reload/restart yourself).
- `reload` — call `homeassistant.reload_all` to reload YAML without a restart.
- `restart` — restart Home Assistant Core.

When `check_config_before_apply` is on (default), the configuration is validated
first and the reload/restart is skipped if it is invalid — so a broken restore
won't take Core down.

This uses the Home Assistant Core API (`homeassistant_api`).

## The dashboard

Open the add-on's **Web UI** (also available as the *Git Sync* sidebar panel for
admins) to see:

- connection details, current branch and strategy;
- the latest commit, whether there are pending local changes, and how far
  ahead/behind the branch you are;
- automation status (timer, file-watcher) and the result of the last run;
- a live activity log;
- the active environment (DEV or PROD) with **Switch** and **Promote DEV → PROD**
  controls (when `allow_branch_switch` is enabled);
- one-click **Sync / back up now** and **Restore from branch** buttons.

## Security notes

- The repository **must be private**. It can contain `secrets.yaml` and other
  configuration you do not want public.
- Access tokens are stored in the add-on's protected options and passed to Git
  only through an in-memory credential helper — they are not written to disk in
  the config directory or committed.
- SSH private keys live only in the add-on's `/data` volume with `0600`
  permissions.
- `.storage` (which holds auth tokens and credentials) is excluded by default.
- Prefer a **Project Access Token** or **Deploy Key** scoped to the single
  configuration repository over a broad personal token.

## Troubleshooting

**`Authentication failed` / `403`** — check the token scope (`write_repository`)
and that the token/deploy key has *write* access to the repository. For project
tokens, the role must be `Developer` or higher.

**`SSL certificate problem`** — your private GitLab uses a certificate the
add-on doesn't trust. Set `verify_ssl: false` (acceptable on a trusted local
network) or install a properly trusted certificate.

**`Host key verification failed` (SSH)** — the add-on trusts the host on first
connection. If the server key changed, remove the add-on's stored
`/data/ssh/known_hosts` by reinstalling, or switch to token auth.

**`conflict` status** — local and remote changed the same lines. Your files are
left untouched. Either reconcile manually, or press **Restore** to take the
remote version (this discards the conflicting local change).

**Nothing is being backed up** — confirm `repository_url` is set, `enabled` is
`true`, and check the add-on log. Files matched by `.gitignore` (database, logs,
`.storage`) are intentionally skipped.

**A new Home Assistant didn't pick up my `prod` branch** — on first run the
add-on adopts an existing remote branch automatically. If the repo already had a
local `.git`, use **Restore** (or `restore_on_start`) instead.

## FAQ

**Does this work with GitHub/Gitea/Bitbucket too?** It is built and tested for
GitLab, but any Git server that accepts HTTPS-token or SSH pushes will work — set
`repository_url` accordingly.

**Will it commit my database and fill up the repo?** No. The database, logs and
caches are excluded by the managed `.gitignore`.

**Can I keep using Git in `/config` myself?** The add-on manages the `origin`
remote and a managed block in `.gitignore`; anything you add outside that block
is preserved. Avoid running conflicting Git operations while it is active.

**How do I temporarily pause it?** Set `enabled: false`. The dashboard and its
manual buttons keep working.
