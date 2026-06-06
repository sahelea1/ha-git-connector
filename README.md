# Riti Git Sync — Home Assistant Add-on

A single-purpose Home Assistant add-on repository for **Riti Git Sync**.

Continuously back up your Home Assistant configuration (`configuration.yaml`,
`automations.yaml`, dashboards, packages, …) to a **private GitLab repository**,
and **restore it from a branch** when something goes wrong. Switch between a
**DEV** sandbox branch and your stable **PROD** branch right from the dashboard.

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsahelea1%2Fha-git-connector)

<p align="center">
  <img src="./gitlab_config_sync/logo.png" alt="Riti Git Sync" width="540">
</p>

## Add this repository

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top-right) → **Repositories**.
3. Paste this URL and click **Add**:

   ```
   https://github.com/sahelea1/ha-git-connector
   ```

4. Close the dialog. **Riti Git Sync** now appears at the bottom of the
   Add-on Store — install it, fill in `repository_url` + `token`, start it and
   open the Web UI.

> Requires **Home Assistant OS** or **Supervised** (the add-on system). It is
> not available on Home Assistant Container or Core installations.

## Features

- ⏱️ Automatic backups on a schedule **and** moments after files change.
- 🌿 Keeps a recovery branch (default `prod`) — fix the files there and Home
  Assistant pulls them back and gets running again.
- ↔️ Two-way sync, mirror-down, or backup-only strategies.
- 🔐 Token (HTTPS) or SSH authentication; credentials are never written into the
  repository.
- 🧰 Self-hosted / private GitLab friendly (custom domains, self-signed certs).
- 🪄 Optional config check + reload/restart after a restore.
- 🧪 DEV/PROD environments — run from a `dev` sandbox branch, test, then promote
  **DEV → PROD** to publish, all from the dashboard.
- ✨ A clean Ingress dashboard (new रीति / crimson editorial theme) with
  one-click **Sync** and **Restore**.

➡️ **[Read the full add-on documentation](./gitlab_config_sync/DOCS.md)**

## Repository layout

```
ha-git-connector/
├── repository.yaml              # Add-on repository manifest (required)
├── gitlab_config_sync/          # The add-on
│   ├── config.yaml              # Add-on manifest
│   ├── build.yaml               # Per-architecture base images
│   ├── Dockerfile               # Image build
│   ├── icon.png / logo.png      # Store artwork
│   ├── README.md / DOCS.md      # Store description & documentation
│   ├── CHANGELOG.md
│   ├── translations/            # Configuration UI labels
│   ├── rootfs/                  # s6-overlay service definitions
│   └── app/                     # Python application
├── tests/                       # Integration tests against a real git repo
└── .github/workflows/           # Linting & validation
```

## License

Released under the [MIT License](./LICENSE).
