"""The reviewer's policy: config file, AGY settings, environment, shims, quota."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agy_bridge import config, paths, policy, selfcheck


def write_config(data: dict) -> None:
    paths.CONFIG_FILE.write_text(json.dumps(data))


@pytest.fixture
def cfg_state(state: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "CONFIG_FILE", state / "config.json")
    monkeypatch.setattr(paths, "SHIMS_DIR", state / "shims")
    return state


def test_config_defaults_without_file(cfg_state: Path) -> None:
    cfg = config.load()
    assert cfg.default_model is None and cfg.sandbox is True
    assert cfg.quota_warn_below == 0.2


def test_sandbox_off_paths_cover_subfolders(cfg_state: Path, tmp_path: Path) -> None:
    site = tmp_path / "LocalSites" / "shop"
    (site / "app" / "public").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    write_config({"sandbox_off_paths": [str(site)]})
    cfg = config.load()
    assert cfg.sandbox_for(str(site)) is False
    assert cfg.sandbox_for(str(site / "app" / "public")) is False
    assert cfg.sandbox_for(str(other)) is True
    assert cfg.sandbox_for(str(tmp_path / "LocalSites" / "shop-2")) is True  # not a prefix


@pytest.mark.parametrize("data", [{"sandbox": "no"}, {"deny_commands": "ssh"},
                                  {"quota_warn_below": 2}, {"default_model": 3}])
def test_broken_config_is_an_error(cfg_state: Path, data: dict) -> None:
    write_config(data)
    with pytest.raises(ValueError):
        config.load()


def test_invalid_json_is_an_error(cfg_state: Path) -> None:
    paths.CONFIG_FILE.write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        config.load()


def test_settings_restore_default_mode_and_scope_writes(cfg_state: Path) -> None:
    s = json.loads(policy.settings(config.Config(), "/s/abc", sandbox=True))
    assert s["agentMode"] == ""  # Auto mode would approve edits in the project
    assert s["permissions"]["allow"] == ["command(regex:.*)", "write_file(/s/abc/)"]
    deny = s["permissions"]["deny"]
    assert "command(regex:.*ssh .*)" in deny and "command(regex:.*git push.*)" in deny
    assert not any("checkout" in r for r in deny)  # the OS sandbox covers the project


def test_unsandboxed_settings_also_deny_git_writes_and_user_rules(cfg_state: Path) -> None:
    cfg = config.Config(deny_commands=(".*make deploy.*",))
    deny = json.loads(policy.settings(cfg, "/s", sandbox=False))["permissions"]["deny"]
    assert {"command(regex:.*git checkout.*)", "command(regex:.*git reset.*)"} <= set(deny)
    assert "command(regex:.*make deploy.*)" in deny


def test_flags() -> None:
    assert policy.flags(True) == ["--sandbox"] and policy.flags(False) == []


def test_deny_patterns_full_match_what_they_should(cfg_state: Path) -> None:
    import re
    rules = [re.compile(r) for r in policy.deny_patterns(config.Config(), sandbox=False)]
    for r in rules:  # only literal text and .*: what AGY's rule parser handles
        assert "\\" not in r.pattern and "|" not in r.pattern, r.pattern

    def denied(cmd: str) -> bool:
        return any(r.fullmatch(cmd) for r in rules)

    for cmd in ("ssh prod gp wp plugin list", "bash -c 'ssh host'", "/usr/bin/ssh -V",
                "git push origin main", "cd x && git push", "wp db reset --yes",
                "wp search-replace a b", "docker compose down", "mysql -u root",
                "git checkout -- .", "git stash", "gh pr merge 1"):
        assert denied(cmd), cmd
    for cmd in ("npm test", "git status --short", "git diff main...HEAD",
                "wp plugin list", "docker compose ps", "pytest -q", "grep -rn sshd ."):
        assert not denied(cmd), cmd


def test_environment_drops_credentials_and_puts_shims_first(cfg_state: Path) -> None:
    base = {"PATH": "/usr/bin", "HOME": "/h", "GH_TOKEN": "x", "SSH_AUTH_SOCK": "/s",
            "CLOUDFLARE_API_TOKEN": "x", "MY_SECRET": "x", "GEMINI_API_KEY": "k",
            "AGY_BIN": "/a", "KEEP_TOKEN": "x"}
    env = policy.environment(config.Config(env_keep=frozenset({"KEEP_TOKEN"})), base)
    assert set(env) == {"PATH", "HOME", "GEMINI_API_KEY", "AGY_BIN", "KEEP_TOKEN"}
    assert env["PATH"].split(":")[0] == str(paths.SHIMS_DIR)


def test_shims_refuse(cfg_state: Path) -> None:
    policy.ensure_shims()
    out = subprocess.run([str(paths.SHIMS_DIR / "ssh"), "prod"], capture_output=True,
                         text=True)
    assert out.returncode == 126 and "blocked" in out.stderr


def test_git_shim_refuses_push_only(cfg_state: Path, tmp_path: Path) -> None:
    policy.ensure_shims()
    git = str(paths.SHIMS_DIR / "git")
    push = subprocess.run([git, "-C", str(tmp_path), "push"], capture_output=True, text=True)
    assert push.returncode == 126 and "git push" in push.stderr
    ok = subprocess.run([git, "--version"], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.startswith("git version")


GROUPS = [
    {"group": "Gemini Models", "models": "", "buckets": [
        {"id": "gemini-weekly", "window": "weekly", "remaining": 0.5, "reset_time": "W"},
        {"id": "gemini-5h", "window": "5h", "remaining": 0.1, "reset_time": "H"}]},
    {"group": "Claude and GPT models", "models": "", "buckets": [
        {"id": "3p-weekly", "window": "weekly", "remaining": 0.05, "reset_time": "W"}]},
]


def test_quota_warnings_follow_the_models_group() -> None:
    assert selfcheck.quota_warnings(GROUPS, "gemini-4-neon-high", 0.2) == [
        "Gemini Models: 5h limit at 10%, resets H"]
    assert selfcheck.quota_warnings(GROUPS, "claude-opus-5-5-high", 0.2) == [
        "Claude and GPT models: weekly limit at 5%, resets W"]
    assert len(selfcheck.quota_warnings(GROUPS, None, 0.2)) == 2  # unknown: every group
    assert selfcheck.quota_warnings(GROUPS, "gemini-4-neon-high", 0.05) == []
