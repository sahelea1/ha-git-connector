# Changelog

All notable changes to this add-on are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## 1.1.0 - 2026-06-06

### Changed

- **Rebranded to Riti Git Sync.** The product display name changed from
  "GitLab Config Sync" to **Riti Git Sync**. The add-on slug, repository URL and
  update path are unchanged, so existing installs update in place.
- **Full visual redesign of the dashboard** with a new रीति / crimson editorial
  theme.

### Added

- **DEV / PROD environment switching.** Keep a stable PROD branch (the existing
  `branch` option, default `prod`) and a `dev` sandbox. Switch the active branch
  Home Assistant runs from directly on the dashboard, test changes on `dev`, then
  **Promote DEV → PROD** to publish your tested configuration to prod.
- New config options `dev_branch` (default `dev`) and `allow_branch_switch`
  (default `true`, lets you switch and promote from the dashboard).
- New dashboard actions / endpoints for **switch** (change active environment)
  and **promote** (DEV → PROD). The configured `apply_action`
  (`none` / `reload` / `restart`) is honoured on switch and promote.

## 1.0.0 - 2026-06-06

Initial release. 🎉

### Added

- Continuous backup of the Home Assistant configuration to a private GitLab
  repository, on a timer and moments after files change.
- Restore-from-branch recovery: pull a known-good branch (default `prod`) back
  into Home Assistant from the dashboard, on start, or continuously.
- Three sync strategies: `rebase` (two-way), `remote_wins` (mirror down) and
  `local_wins` (backup only).
- Token (HTTPS) and SSH authentication, with credentials kept out of the
  repository and the config directory.
- Managed `.gitignore` that excludes the database, logs, `.storage` and other
  sensitive/volatile state, with user-defined extra excludes.
- Optional configuration check followed by `reload_all` or a Core restart after
  a restore.
- Support for self-hosted/private GitLab, including a `verify_ssl` toggle for
  self-signed certificates.
- Sleek Ingress dashboard with live status, activity log and one-click
  **Sync**/**Restore**.
- Multi-architecture images: `aarch64`, `amd64`, `armhf`, `armv7`, `i386`.
