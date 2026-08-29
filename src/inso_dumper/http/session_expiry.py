"""Map missing/invalid session state to the user-facing SESSION_EXPIRED error.

This is the only place in the codebase that turns a missing
``session.json`` into a ``SESSION_EXPIRED``. Centralizing the
mapping means the CLI never has to reason about file presence.
"""

from __future__ import annotations

from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.session import Session
from inso_dumper.session.store import maybe_load_session


def ensure_session_loaded(path: Path) -> CliResult[Session]:
    """Load a Session from ``path`` or surface SESSION_EXPIRED.

    Returns:
      - ``Ok(Session)`` if the file exists and validates.
      - ``Err(SESSION_EXPIRED)`` if the file is missing.
      - ``Err(CONFIG)`` if the file is unparseable or has a wrong
        schema version (the user's data is corrupt; re-login won't
        fix it without manual intervention).
    """
    result = maybe_load_session(path)
    match result:
        case Ok(None):
            return Err(CliError(kind=CliErrorKind.SESSION_EXPIRED, subject="session_missing"))
        case Ok(value):
            assert value is not None  # narrowed by the None branch above
            return Ok(value)
        case Err(error):
            return Err(error)


__all__ = ["ensure_session_loaded"]
