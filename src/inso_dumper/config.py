"""Config model and loader.

``Config`` is the typed settings surface (pure data). ``load_config`` is
the only function in the codebase that reads the env or the filesystem
to assemble one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.paths import config_file


class Config(BaseModel):
    """Typed settings for an inso-dumper run.

    Credentials are NOT in this model — they are read directly from
    ``INSO_EMAIL`` / ``INSO_PASSWORD`` at the auth boundary to keep
    them out of logs and reprs.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl = HttpUrl("https://app.inso.pl")
    rate_limit_per_second: float = 3.0
    request_timeout_seconds: float = 30.0


def _read_toml(path: Path) -> dict[str, Any] | None:
    """Read a TOML file and return its raw dict, or None if the file is missing."""
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(path: Path | None = None) -> Ok[Config] | Err[CliError]:
    """Assemble a Config from TOML and return it as a Result.

    A missing config file is not an error: defaults are the missing-file
    case. A present-but-unparseable file is a ``CONFIG`` error.
    """
    target = path if path is not None else config_file()
    try:
        raw = _read_toml(target)
    except (OSError, tomllib.TOMLDecodeError):
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="config_parse"))
    if raw is None:
        return Ok(Config())
    try:
        return Ok(Config(**raw))
    except Exception:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="config_parse"))
