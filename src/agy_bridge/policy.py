"""How an AGY reviewer process is allowed to act: its settings, flags and environment.

Measured on AGY 1.5.16 (see docs/architecture.md, "Policy"):

- With no saved agent mode AGY starts in Auto, which approves file edits inside the
  workspace whatever `permissions.allow` says. `"agentMode": ""` restores the old
  behaviour: anything not allowed is denied.
- `--sandbox` makes the project read-only to shell commands at the OS level, but also
  blocks `~/Library/Application Support` (Local's MySQL sockets), writes to the
  project's `.git` (so no `git worktree add`), and proxies localhost.
- Command rules are full-match regexes, `deny` beats `allow`, and a denied command
  returns an error to AGY without ending its turn. AGY's rule parser is narrower than the
  word "regex" suggests: backslash escapes (`\\s`, `\\b`) and top-level `|` did not work,
  while literal text, spaces and `.*` did. So every rule here is literal text wrapped
  in `.*`.

The deny list and the shims stop honest mistakes (an `ssh` to production, a
`git push`). They are not a security boundary: a determined command can get round any
pattern. The sandbox is the boundary; the rest is a seat belt.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from collections.abc import Mapping
from pathlib import Path

from . import paths
from .config import Config

# Remote access and production tooling: never from a reviewer. Literal phrases; each
# becomes `.*<phrase>.*` (see the module docstring for why there are no escapes).
DENY_ALWAYS = (
    "ssh ", "scp ", "sftp ", "rsync ", "gp ", "gh ",
    "git push",
    "mysql ", "mariadb ",
    "wp db reset", "wp db import", "wp db drop", "wp db clean", "wp db query",
    "wp search-replace", "wp eval",
    "docker compose down", "docker compose stop", "docker compose rm",
    "docker compose restart", "docker compose kill",
    "docker-compose down", "docker-compose stop", "docker-compose rm",
    "docker-compose restart", "docker stop ", "docker rm ", "docker restart ",
    "docker kill ", "docker system prune", "docker volume rm",
)
# Without the sandbox, nothing stops a shell command rewriting the project or its git
# state, so these are denied as well. In the sandbox the OS already refuses them, and
# AGY may still need them in its own clone in the scratch folder.
DENY_UNSANDBOXED = tuple(f"git {sub}" for sub in (
    "commit", "reset", "clean", "checkout", "switch", "stash", "rebase", "merge",
    "restore", "cherry-pick", "revert", "am ", "apply"))
# Programs replaced by a script that refuses, first on PATH: catches what a pattern
# misses, such as `bash -c 'ssh …'`. An absolute path (/usr/bin/ssh) still gets past.
SHIMMED = ("ssh", "scp", "sftp", "rsync", "gp", "gh")
# git stays usable; its shim refuses only these subcommands, then runs the real git.
GIT_REFUSED = ("push",)
_PATTERN_SAFE = re.compile(r"[A-Za-z0-9 _./:=-]+")

# Environment variables that look like credentials are not passed to AGY, so commands
# it runs can't use them. AGY's own sign-in lives in the keychain, not the environment.
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|PRIVATE_KEY|CREDENTIAL|AUTH")
_SECRET_PREFIX = ("AWS_", "CLOUDFLARE_", "CF_", "GITHUB_", "GH_", "SSH_")
_KEEP = frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                   "AGY_ADC_AUTH"})


def deny_patterns(cfg: Config, sandbox: bool) -> list[str]:
    """Full-match patterns, as AGY applies them: built-in phrases, then the user's own."""
    phrases = [*DENY_ALWAYS, *([] if sandbox else DENY_UNSANDBOXED)]
    for ph in phrases:  # a guard for this file: a metacharacter would silently not match
        assert _PATTERN_SAFE.fullmatch(ph), ph
    return [f".*{ph}.*" for ph in phrases] + list(cfg.deny_commands)


def deny_rules(cfg: Config, sandbox: bool) -> list[str]:
    return [f"command(regex:{r})" for r in deny_patterns(cfg, sandbox)]


def settings(cfg: Config, scratch: str, sandbox: bool) -> str:
    """The `--settings` JSON for one reviewer process."""
    return json.dumps({
        "agentMode": "",
        "permissions": {"allow": ["command(regex:.*)", f"write_file({scratch}/)"],
                        "deny": deny_rules(cfg, sandbox)},
    })


def flags(sandbox: bool) -> list[str]:
    return ["--sandbox"] if sandbox else []


def _is_secret(name: str, keep: frozenset[str]) -> bool:
    if name in _KEEP or name in keep or name.startswith("AGY_"):
        return False
    return name.startswith(_SECRET_PREFIX) or bool(_SECRET_NAME.search(name.upper()))


def environment(cfg: Config, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for AGY: credentials removed, the shims first on PATH."""
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if not _is_secret(k, cfg.env_keep)}
    ensure_shims()
    env["PATH"] = os.pathsep.join([str(paths.SHIMS_DIR), env.get("PATH", "")])
    return env


def removed_variables(cfg: Config, base: Mapping[str, str] | None = None) -> list[str]:
    base = os.environ if base is None else base
    return sorted(k for k in base if _is_secret(k, cfg.env_keep))


def _refuse(name: str) -> str:
    return (f"#!/bin/sh\necho 'agy-bridge: `{name}` is blocked for AGY reviewers.' >&2\n"
            "exit 126\n")


def _git_shim(real_git: str) -> str:
    cases = "|".join(GIT_REFUSED)
    return ("#!/bin/sh\n"
            "# agy-bridge: refuse git subcommands a reviewer must never run, pass the rest on.\n"
            "for a in \"$@\"; do\n"
            f"  case \"$a\" in {cases})\n"
            "    echo \"agy-bridge: \\`git $a\\` is blocked for AGY reviewers.\" >&2; exit 126;;\n"
            "  esac\n"
            "done\n"
            f"exec {shlex.quote(real_git)} \"$@\"\n")


def ensure_shims() -> None:
    paths.SHIMS_DIR.mkdir(parents=True, exist_ok=True)
    shims = {name: _refuse(name) for name in SHIMMED}
    search = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                             if d and Path(d) != paths.SHIMS_DIR)
    if real_git := shutil.which("git", path=search):
        shims["git"] = _git_shim(real_git)
    for name, body in shims.items():
        shim = paths.SHIMS_DIR / name
        if not shim.is_file() or shim.read_text() != body:
            shim.write_text(body)
        shim.chmod(0o755)
