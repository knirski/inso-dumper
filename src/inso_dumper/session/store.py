"""Session JSON store with atomic write, 0o600 mode, and schema gate.

The store's Err surface is exactly ``{CONFIG}``:
  - missing file is NOT an error (state, not configuration);
    callers use ``maybe_load_session`` to distinguish.
  - unparseable JSON → ``CONFIG`` with subject ``"session_schema"``
  - schema_version != 1 → same.
  - file IO failures → ``CONFIG`` with subject ``"session_io"`` so the CLI
    can show "your session file is broken" without leaking OS details.

This module is the only place that touches the session file on disk.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.session import Session

_FILE_MODE = 0o600


def _tighten_mode(path: Path) -> None:
    with contextlib.suppress(OSError):
        # Best-effort; if the filesystem does not support chmod the loader
        # still returns the parsed session.
        os.chmod(path, _FILE_MODE)


def save_session(path: Path, session: Session) -> CliResult[None]:
    """Atomically write the session JSON to ``path`` with mode 0o600."""
    target = path
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        # Ensure parent exists.
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to .tmp with restrictive mode, then atomically replace.
        fd = os.open(
            str(tmp),
            flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            mode=_FILE_MODE,
        )
        try:
            data = session.model_dump_json().encode("utf-8")
            # Loop until the entire payload is on disk. ``os.write`` on
            # blocking file descriptors is allowed to return fewer
            # bytes than requested without raising (e.g. when the write
            # is interrupted by a signal); the previous code accepted
            # the short write and then ``os.replace`` swapped the
            # valid prior session for the truncated JSON.
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written == 0:
                    raise OSError("short write to session file")
                view = view[written:]
            # fsync the data so a power-cut between os.write and
            # os.replace doesn't leave the real file pointing at zero
            # bytes (forcing an unnecessary re-login).
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
        _tighten_mode(target)
    except OSError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="session_io"))
    return Ok(None)


def _load_from_path(path: Path) -> CliResult[Session]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="session_io"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="session_schema"))
    if not isinstance(data, dict):
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="session_schema"))
    try:
        session = Session.model_validate(data)
    except ValidationError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="session_schema"))
    _tighten_mode(path)
    return Ok(session)


def maybe_load_session(path: Path) -> CliResult[Session | None]:
    """Like ``load_session`` but returns ``Ok(None)`` for a missing file."""
    if not path.is_file():
        return Ok(None)
    inner = _load_from_path(path)
    # Re-narrow the success type: the file existed, so a successful load
    # is definitely a Session, never None.
    match inner:
        case Ok(value):
            return Ok(value)
        case Err(error):
            return Err(error)


__all__ = [
    "maybe_load_session",
    "save_session",
]
