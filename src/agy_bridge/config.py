"""The user's settings, from ~/.agy-bridge/config.json.

The file lives in the state folder, never in the reviewed project, so a project can't
switch off its own sandbox or blocklist. Every key is optional:

  default_model      model slug used when Claude names none, e.g. "gemini-4-neon-high"
  sandbox            true (default): run AGY with --sandbox, so the project is read-only
  sandbox_off_paths  project folders (and everything under them) reviewed without the
                     sandbox, because AGY must reach a running environment there
  deny_commands      extra full-match command regexes to deny, added to the built-in list
  env_keep           environment variables to pass to AGY even if they look like secrets
  quota_warn_below   warn when a quota bucket's remaining fraction drops below (0.2)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths


@dataclass(frozen=True)
class Config:
    default_model: str | None = None
    sandbox: bool = True
    sandbox_off_paths: tuple[Path, ...] = ()
    deny_commands: tuple[str, ...] = ()
    env_keep: frozenset[str] = field(default_factory=frozenset)
    quota_warn_below: float = 0.2

    def sandbox_for(self, cwd: str) -> bool:
        """Whether a review of `cwd` runs in the sandbox, unless the caller says otherwise."""
        project = Path(cwd).resolve()
        if any(p == project or p in project.parents for p in self.sandbox_off_paths):
            return False
        return self.sandbox


def _strings(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{paths.CONFIG_FILE}: `{key}` must be a list of strings")
    return value


def load() -> Config:
    """Read the config file. A missing file means defaults; a broken one is an error,
    since silently falling back could drop a sandbox exception or a deny rule."""
    try:
        raw = json.loads(paths.CONFIG_FILE.read_text())
    except FileNotFoundError:
        return Config()
    except json.JSONDecodeError as e:
        raise ValueError(f"{paths.CONFIG_FILE} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{paths.CONFIG_FILE} must hold a JSON object")
    model = raw.get("default_model")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"{paths.CONFIG_FILE}: `default_model` must be a string")
    sandbox = raw.get("sandbox", True)
    if not isinstance(sandbox, bool):
        raise ValueError(f"{paths.CONFIG_FILE}: `sandbox` must be true or false")
    warn = raw.get("quota_warn_below", 0.2)
    if not isinstance(warn, int | float) or not 0 <= warn <= 1:
        raise ValueError(f"{paths.CONFIG_FILE}: `quota_warn_below` must be between 0 and 1")
    return Config(
        default_model=model or None,
        sandbox=sandbox,
        sandbox_off_paths=tuple(Path(p).expanduser().resolve()
                                for p in _strings(raw.get("sandbox_off_paths"),
                                                  "sandbox_off_paths")),
        deny_commands=tuple(_strings(raw.get("deny_commands"), "deny_commands")),
        env_keep=frozenset(_strings(raw.get("env_keep"), "env_keep")),
        quota_warn_below=float(warn),
    )
