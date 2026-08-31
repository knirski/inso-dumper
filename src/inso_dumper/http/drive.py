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
from collections.abc import AsyncIterator

from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import HttpClient
from inso_dumper.models.children import _SLUG_RE, _ascii_fold
from inso_dumper.models.drive import DriveListing
from inso_dumper.models.session import Session

_DRIVE_ITEMS_PATH = "/drive/items"


def _slugify_directory(name: str) -> str:
    """The shared children/post slug rule applied to a path component.

    Platform-supplied directory names must never carry ``/``, ``\\``, or
    ``..`` into the dump path; one slugified segment cannot escape.
    """
    return _SLUG_RE.sub("-", _ascii_fold(name).lower()).strip("-")


def _platform_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="drive_listing"))


def _drive_node_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="drive_node"))


def parse_drive_listing(body: bytes) -> CliResult[DriveListing]:
    """Parse one ``/drive/items`` envelope, drift-fail on any unknown key.

    Malformed JSON (including non-UTF-8 bytes) or any unknown key in the
    envelope or the verified ``breadcrumbs`` is ``Err(PLATFORM_CHANGED)``.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return _platform_changed()
    try:
        return Ok(DriveListing.model_validate(data))
    except ValidationError:
        return _platform_changed()


def _directory_entry(entry: object) -> CliResult[tuple[str, str]]:
    """Defensively extract ``(id, name)`` from a provisional directory
    node. The shapes are unverified (empty drive at spike time), so
    anything without a usable id/name is drift, not a guess."""
    if not isinstance(entry, dict):
        return _drive_node_changed()
    directory_id = entry.get("id")
    name = entry.get("name")
    if not isinstance(directory_id, str) or not directory_id:
        return _drive_node_changed()
    if not isinstance(name, str) or not name.strip():
        return _drive_node_changed()
    return Ok((directory_id, name))


async def _fetch_listing(
    client: HttpClient, session: Session, path: str
) -> CliResult[DriveListing]:
    """One authenticated ``GET /drive/items`` with status/drift mapping."""
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    result = await client.request("GET", path, headers=headers)
    response = None
    match result:
        case Err(error):
            return Err(error)
        case Ok(value):
            response = value
    if response.status != 200:
        return Err(CliError(kind=CliErrorKind.HTTP, subject=f"drive_status_{response.status}"))
    return parse_drive_listing(response.body)


async def walk_drive(
    client: HttpClient, session: Session
) -> AsyncIterator[CliResult[tuple[str, DriveListing]]]:
    """Depth-first walk of the drive tree.

    Yields ``(relative_path, listing)`` per directory — ``("", listing)``
    for the root — fetching one listing per directory. ``relative_path``
    is built from slugified directory names (the shared slug rule), so a
    platform-supplied name containing ``/``, ``\\``, or ``..`` cannot
    escape ``dump/documents/``; an empty slug falls back to
    ``directory``. Read-only: no ``/drive/directory*`` or
    ``/drive/file*`` write endpoint is ever requested.
    """
    root_result = await _fetch_listing(client, session, _DRIVE_ITEMS_PATH)
    root_listing: DriveListing | None
    match root_result:
        case Err(error):
            yield Err(error)
            return
        case Ok(listing):
            root_listing = listing
    yield Ok(("", root_listing))
    pending: list[tuple[str, object]] = [("", d) for d in root_listing.directories]
    while pending:
        parent_path, entry = pending.pop(0)
        entry_result = _directory_entry(entry)
        match entry_result:
            case Err(error):
                yield Err(error)
                return
            case Ok((directory_id, name)):
                pass
        slug = _slugify_directory(name) or "directory"
        relative_path = f"{parent_path}/{slug}" if parent_path else slug
        listing_result = await _fetch_listing(
            client, session, f"{_DRIVE_ITEMS_PATH}/{directory_id}"
        )
        sub_listing: DriveListing | None
        match listing_result:
            case Err(error):
                yield Err(error)
                return
            case Ok(listing):
                sub_listing = listing
        yield Ok((relative_path, sub_listing))
        pending[:0] = [(relative_path, d) for d in sub_listing.directories]


__all__ = ["parse_drive_listing", "walk_drive"]
