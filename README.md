# Sahelea Home Assistant Add-ons

A Home Assistant add-on repository. Add it to your Supervisor and install the
add-ons below directly from the Home Assistant UI.

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsahelea1%2Fha-git-connector)

## Add this repository

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top-right) → **Repositories**.
3. Paste this URL and click **Add**:

   ```
   https://github.com/sahelea1/ha-git-connector
   ```

4. Close the dialog. The add-ons below now appear at the bottom of the store.

> Requires **Home Assistant OS** or **Supervised** (the add-on system). It is
> not available on Home Assistant Container or Core installations.

## Add-ons in this repository

### 🔄 [GitLab Config Sync](./gitlab_config_sync)

Continuously back up your Home Assistant configuration (`configuration.yaml`,
`automations.yaml`, dashboards, packages, …) to a **private GitLab repository**,
and **restore it from a branch** when something goes wrong.

- ⏱️ Automatic backups on a schedule **and** whenever files change.
- 🌿 Keeps a recovery branch (default `prod`) — fix the files there and Home
  Assistant pulls them back and gets running again.
- ↔️ Two-way sync, mirror-down, or backup-only strategies.
- 🔐 Token (HTTPS) or SSH authentication; credentials are never written into the
  repository.
- 🧰 Self-hosted / private GitLab friendly (custom domains, self-signed certs).
- 🪄 Optional config check + reload/restart after a restore.
- ✨ A clean Ingress dashboard with one-click **Sync** and **Restore**.

<p align="center">
  <img src="./gitlab_config_sync/logo.png" alt="GitLab Config Sync" width="520">
</p>

➡️ **[Read the full documentation](./gitlab_config_sync/DOCS.md)**

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
└── .github/workflows/           # Linting & validation
```

## License

Released under the [MIT License](./LICENSE).
