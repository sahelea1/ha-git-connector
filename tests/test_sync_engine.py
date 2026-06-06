"""Integration tests for the sync engine.

These exercise the real git plumbing against a local ``file://`` repository
that stands in for the private GitLab server, so no network is required.
"""
import os
import subprocess

import pytest

import app.config as config_module
from app.config import Options
from app.logsetup import setup_logging
from app.manager import SyncManager
from app.supervisor import Supervisor

setup_logging("warning")


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def write(directory, rel, content):
    path = os.path.join(directory, rel)
    os.makedirs(os.path.dirname(path) or directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def read(directory, rel):
    with open(os.path.join(directory, rel), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A bare remote, an empty config dir and a data dir, wired into Options."""
    remote = tmp_path / "remote.git"
    config = tmp_path / "config"
    data = tmp_path / "data"
    remote.mkdir()
    config.mkdir()
    data.mkdir()
    git("init", "--bare", "-b", "prod", str(remote), cwd=str(tmp_path))

    monkeypatch.setenv("GITSYNC_CONFIG_PATH", str(config))
    monkeypatch.setattr(config_module, "DATA_DIR", str(data))
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    return {"remote": str(remote), "config": str(config), "tmp": str(tmp_path)}


def make_manager(env, **overrides):
    opts = Options(
        repository_url="file://" + env["remote"],
        branch="prod",
        auth_method="token",
        token="",
        commit_name="HA Test",
        commit_email="ha@test.local",
        sync_strategy=overrides.pop("sync_strategy", "rebase"),
        enabled=True,
        watch_changes=False,
        apply_action="none",
        **overrides,
    )
    return SyncManager(opts, Supervisor())


def remote_files(env, branch="prod"):
    work = os.path.join(env["tmp"], "verify_clone")
    subprocess.run(["rm", "-rf", work], check=False)
    git("clone", "-b", branch, env["remote"], work, cwd=env["tmp"])
    files = set()
    for dirpath, _, filenames in os.walk(work):
        if ".git" in dirpath:
            continue
        for name in filenames:
            files.add(os.path.relpath(os.path.join(dirpath, name), work))
    return files, work


def test_first_sync_seeds_remote_and_excludes_sensitive(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Home\n")
    write(cfg, "automations.yaml", "[]\n")
    write(cfg, "secrets.yaml", "api_key: SECRET\n")
    write(cfg, ".storage/auth", "TOKENS")
    write(cfg, "home-assistant_v2.db", "DBDATA")
    write(cfg, "home-assistant.log", "logs")

    make_manager(env).sync("test")

    files, _ = remote_files(env)
    assert "configuration.yaml" in files
    assert "automations.yaml" in files
    assert "secrets.yaml" in files
    assert ".gitignore" in files
    assert not any(".storage" in f for f in files)
    assert "home-assistant_v2.db" not in files
    assert "home-assistant.log" not in files


def test_local_change_is_backed_up(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Home\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    write(cfg, "automations.yaml", "- alias: New\n")
    mgr.sync("change")

    _, work = remote_files(env)
    assert "alias: New" in read(work, "automations.yaml")


def test_restore_pulls_branch_back(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Original\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    # Simulate a user fixing the prod branch directly in GitLab.
    edit = os.path.join(env["tmp"], "edit")
    git("clone", "-b", "prod", env["remote"], edit, cwd=env["tmp"])
    write(edit, "configuration.yaml", "homeassistant:\n  name: Recovered\n")
    git("-c", "user.email=u@u", "-c", "user.name=u", "commit", "-am", "fix", cwd=edit)
    git("push", "origin", "prod", cwd=edit)

    mgr.restore("recover")
    assert "Recovered" in read(cfg, "configuration.yaml")


def test_two_way_rebase_merges_changes(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env, sync_strategy="rebase")
    mgr.sync("seed")

    # Remote change on a different file.
    edit = os.path.join(env["tmp"], "edit")
    git("clone", "-b", "prod", env["remote"], edit, cwd=env["tmp"])
    write(edit, "scripts.yaml", "remote_script: {}\n")
    git("-c", "user.email=u@u", "-c", "user.name=u", "add", "-A", cwd=edit)
    git("-c", "user.email=u@u", "-c", "user.name=u", "commit", "-m", "s", cwd=edit)
    git("push", "origin", "prod", cwd=edit)

    # Local change on yet another file.
    write(cfg, "groups.yaml", "local_group: {}\n")
    mgr.sync("merge")

    assert "remote_script" in read(cfg, "scripts.yaml")
    files, work = remote_files(env)
    assert "groups.yaml" in files
    assert "remote_script" in read(work, "scripts.yaml")


def test_remote_wins_mirrors_and_discards_local(env):
    cfg = env["config"]
    # Pre-populate prod with a golden config.
    seed = os.path.join(env["tmp"], "seed")
    git("clone", env["remote"], seed, cwd=env["tmp"])
    write(seed, "configuration.yaml", "homeassistant:\n  name: Golden\n")
    git("-c", "user.email=u@u", "-c", "user.name=u", "add", "-A", cwd=seed)
    git("-c", "user.email=u@u", "-c", "user.name=u", "commit", "-m", "g", cwd=seed)
    git("push", "origin", "HEAD:prod", cwd=seed)

    # Fresh HA with a default config plus sensitive files and a stray file.
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Default\n")
    write(cfg, "stray.yaml", "leftover")
    write(cfg, ".storage/auth", "PRECIOUS")
    write(cfg, "home-assistant_v2.db", "DB")

    make_manager(env, sync_strategy="remote_wins", restore_clean=True).sync("start")

    assert "Golden" in read(cfg, "configuration.yaml")
    assert os.path.exists(os.path.join(cfg, ".storage/auth"))  # protected
    assert os.path.exists(os.path.join(cfg, "home-assistant_v2.db"))  # protected
    assert not os.path.exists(os.path.join(cfg, "stray.yaml"))  # cleaned


def test_noop_sync_creates_no_commit(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    before = git("rev-list", "--count", "HEAD", cwd=cfg).stdout.strip()
    mgr.sync("noop")
    after = git("rev-list", "--count", "HEAD", cwd=cfg).stdout.strip()
    assert before == after


def test_user_excludes_are_honoured(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    write(cfg, "private_notes.txt", "do not back up")
    make_manager(env, excludes=["private_notes.txt"]).sync("seed")

    files, _ = remote_files(env)
    assert "configuration.yaml" in files
    assert "private_notes.txt" not in files


def test_status_reports_clean_after_sync(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    status = mgr.status()
    assert status["configured"] is True
    assert status["git"]["initialized"] is True
    assert status["git"]["dirty"] is False
    assert status["last"]["result"] == "ok"
