# GitLab Config Sync

![GitLab Config Sync](logo.png)

Continuously back up your Home Assistant configuration to a **private GitLab
repository**, and restore it from a branch when something breaks.

Treat a Git branch (default `prod`) as the source of truth for your setup: the
add-on pushes every change you make, and — if a bad edit ever takes Home
Assistant down — you fix the files on that branch in GitLab and the add-on pulls
them back so your system comes back up.

## Highlights

- ⏱️ **Automatic** — backs up on a schedule *and* moments after you change a
  file.
- 🌿 **Recovery branch** — keep a known-good `prod` branch; restore from it with
  one click or automatically on start.
- ↔️ **Flexible direction** — two-way sync, mirror-down (GitLab wins), or
  backup-only (Home Assistant wins).
- 🔐 **Secure** — HTTPS access token *or* SSH key. Secrets are kept out of the
  repository, and `.storage`, the database and logs are never backed up.
- 🏠 **Private-GitLab ready** — custom domains and self-signed certificates are
  supported.
- 🪄 **Safe apply** — optionally validate the configuration and reload or restart
  Home Assistant after a restore.
- ✨ **Sleek dashboard** — a built-in Ingress panel shows status and offers
  one-click **Sync** and **Restore**.

## Quick start

1. Create an **empty private repository** in your GitLab instance, e.g.
   `ha-config`.
2. Create a **Project Access Token** (or Personal Access Token) with the
   `write_repository` scope.
3. Install this add-on, open **Configuration**, and set:
   - `repository_url` → `https://gitlab.example.com/you/ha-config.git`
   - `auth_method` → `token`
   - `token` → *your access token*
4. **Start** the add-on and open its **Web UI**.

Your configuration is now committed to the `prod` branch and kept in sync.

👉 For the SSH setup, all options, sync strategies and the full recovery
walkthrough, see the **Documentation** tab.

## Recovery in a nutshell

Home Assistant won't start after a bad edit? Open your repository in GitLab, fix
the offending file on the `prod` branch, then either press **Restore** in the
add-on's Web UI or enable `restore_on_start` and restart the add-on. The
known-good configuration is pulled back into place.
