"""Filesystem path resolution.

Pure functions over env-derived inputs. No IO is performed by this module:
``os.environ`` is read at the boundary so the same input yields the same
output, making paths deterministic across runs.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg_path(env_var: str, fallback: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base)
    home = os.environ.get("HOME")
    if not home:
        # Last-ditch: empty path; the loader will report a sensible error.
        return Path(fallback)
    return Path(home) / fallback


def config_file() -> Path:
    """Return the resolved user-level TOML config path.

    Honors ``INSO_DUMPER_CONFIG`` if set, else ``$XDG_CONFIG_HOME``,
    else ``$HOME/.config``.
    """
    override = os.environ.get("INSO_DUMPER_CONFIG")
    if override:
        return Path(override)
    return _xdg_path("XDG_CONFIG_HOME", ".config") / "inso-dumper" / "config.toml"


def state_dir() -> Path:
    """Return the resolved user-level state directory.

    Honors ``$XDG_STATE_HOME`` if set, else ``$HOME/.local/state``.
    """
    return _xdg_path("XDG_STATE_HOME", ".local/state") / "inso-dumper"


def session_file() -> Path:
    """Return the path where the Session JSON is persisted."""
    return state_dir() / "session.json"
