"""Integration tests for the DEV/PROD branch-switching feature.

These reuse the same local ``file://`` remote pattern as ``test_sync_engine``:
a bare repo stands in for the private GitLab server so no network is required.
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

    return {
        "remote": str(remote),
        "config": str(config),
        "data": str(data),
        "tmp": str(tmp_path),
    }


def make_manager(env, **overrides):
    opts = Options(
        repository_url="file://" + env["remote"],
        branch="prod",
        dev_branch="dev",
        allow_branch_switch=overrides.pop("allow_branch_switch", True),
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


def remote_branches(env):
    proc = git("ls-remote", "--heads", env["remote"], cwd=env["tmp"])
    names = set()
    for line in proc.stdout.splitlines():
        if "\t" in line:
            ref = line.split("\t", 1)[1].strip()
            if ref.startswith("refs/heads/"):
                names.add(ref[len("refs/heads/"):])
    return names


def remote_file(env, branch, rel):
    work = os.path.join(env["tmp"], f"verify_{branch}")
    subprocess.run(["rm", "-rf", work], check=False)
    git("clone", "-b", branch, env["remote"], work, cwd=env["tmp"])
    return read(work, rel)


def test_default_active_branch_is_prod(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")
    assert mgr.active_branch == "prod"
    status = mgr.status()
    assert status["active_branch"] == "prod"
    assert status["prod_branch"] == "prod"
    assert status["dev_branch"] == "dev"
    assert status["is_dev"] is False
    assert status["allow_branch_switch"] is True
    assert "prod" in status["branches"]


def test_switch_to_new_branch_creates_and_sets_active(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Prod\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    result = mgr.switch_branch("dev")
    assert result["ok"] is True
    assert mgr.active_branch == "dev"
    assert mgr.repo.current_branch() == "dev"

    status = mgr.status()
    assert status["active_branch"] == "dev"
    assert status["is_dev"] is True

    # The dev branch was created on the remote with the current config.
    assert "dev" in remote_branches(env)
    assert "Prod" in remote_file(env, "dev", "configuration.yaml")


def test_switch_disabled_is_rejected(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env, allow_branch_switch=False)
    mgr.sync("seed")

    result = mgr.switch_branch("dev")
    assert result["ok"] is False
    assert mgr.active_branch == "prod"


def test_switch_to_same_branch_is_noop(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    result = mgr.switch_branch("prod")
    assert result["ok"] is True
    assert "Already" in result["message"]
    assert mgr.active_branch == "prod"


def test_active_branch_persists_across_reinit(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")
    mgr.switch_branch("dev")
    assert mgr.active_branch == "dev"

    # A fresh manager (simulating an add-on restart) reads the persisted state.
    mgr2 = make_manager(env)
    assert mgr2.active_branch == "dev"
    assert mgr2.status()["active_branch"] == "dev"


def test_promote_publishes_dev_onto_prod(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Stable\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    # Switch to dev and make a change there.
    mgr.switch_branch("dev")
    write(cfg, "configuration.yaml", "homeassistant:\n  name: Tested\n")
    mgr.sync("dev-change")
    assert "Tested" in remote_file(env, "dev", "configuration.yaml")
    # Prod still holds the old content.
    assert "Stable" in remote_file(env, "prod", "configuration.yaml")

    # Promote dev -> prod.
    result = mgr.promote()
    assert result["ok"] is True
    assert "Tested" in remote_file(env, "prod", "configuration.yaml")
    # Active branch is left on dev (no auto-switch).
    assert mgr.active_branch == "dev"


def test_promote_on_prod_is_noop(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")

    result = mgr.promote()
    assert result["ok"] is True
    assert "already" in result["message"].lower()


def test_list_branches_includes_remote_and_configured(env):
    cfg = env["config"]
    write(cfg, "configuration.yaml", "homeassistant: {}\n")
    mgr = make_manager(env)
    mgr.sync("seed")
    mgr.switch_branch("dev")

    branches = mgr.list_branches()
    assert "prod" in branches
    assert "dev" in branches
    # De-duplicated.
    assert len(branches) == len(set(branches))
