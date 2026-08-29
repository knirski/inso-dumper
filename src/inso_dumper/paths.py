"""Filesystem path resolution.

Pure functions over env-derived inputs. No IO is performed by this module:
``os.environ`` is read at the boundary so the same input yields the same
output, making paths deterministic across runs.

If both ``$XDG_STATE_HOME`` (or ``$XDG_CONFIG_HOME``) and ``$HOME`` are
unset, the path resolver raises ``RuntimeError`` rather than falling
back to a relative path. A relative path would resolve against the
current working directory, which is unpredictable and can leak
session data into a build tree or repo checkout.
"""

from __future__ import annotations

import os
from pathlib import Path


def _require_home() -> Path:
    """Return ``$HOME`` as a Path, or raise if unset.

    The env lookup is performed at the boundary so the function
    remains a pure function of its (env-derived) inputs.
    """
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError(
            "Cannot resolve a user-level path: $HOME and $XDG_STATE_HOME "
            "(or $XDG_CONFIG_HOME) are both unset. Set HOME for the "
            "current user, or pass an explicit --config / state path."
        )
    return Path(home)


def _xdg_path(env_var: str, fallback: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base)
    return _require_home() / fallback


def config_file() -> Path:
    """Return the resolved user-level TOML config path.

    Honors ``INSO_DUMPER_CONFIG`` if set, else ``$XDG_CONFIG_HOME``,
    else ``$HOME/.config``. Raises ``RuntimeError`` if neither
    ``$XDG_CONFIG_HOME`` nor ``$HOME`` is set.
    """
    override = os.environ.get("INSO_DUMPER_CONFIG")
    if override:
        return Path(override)
    return _xdg_path("XDG_CONFIG_HOME", ".config") / "inso-dumper" / "config.toml"


def state_dir() -> Path:
    """Return the resolved user-level state directory.

    Honors ``$XDG_STATE_HOME`` if set, else ``$HOME/.local/state``.
    Raises ``RuntimeError`` if neither env var is set.
    """
    return _xdg_path("XDG_STATE_HOME", ".local/state") / "inso-dumper"


def session_file() -> Path:
    """Return the path where the Session JSON is persisted."""
    return state_dir() / "session.json"
