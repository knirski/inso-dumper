"""Drive API: provisional models and the listing parser.

Functional core: bytes in, models out, no IO. The drive of the spike
account was empty, so only ``breadcrumbs`` is verified;
``directories``/``directory``/``files`` are deliberately permissive
placeholders pending a non-empty drive capture (the videos precedent:
first real traffic either parses or fails loud, then the models are
tightened from a capture — design Open Question 1).

Endpoint (verified by the Aug 2026 spike):

    GET /drive/items[/{directoryId}]

Read-only GETs only — the ``/drive/directory*`` and ``/drive/file*``
write endpoints are never called.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.drive import DriveListing


def _platform_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="drive_listing"))


def parse_drive_listing(body: bytes) -> CliResult[DriveListing]:
    """Parse one ``/drive/items`` envelope, drift-fail on any unknown key.

    Malformed JSON (including non-UTF-8 bytes) or any unknown key in the
    envelope or the verified ``breadcrumbs`` is ``Err(PLATFORM_CHANGED)``.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _platform_changed()
    try:
        return Ok(DriveListing.model_validate(data))
    except ValidationError:
        return _platform_changed()


__all__ = ["parse_drive_listing"]
