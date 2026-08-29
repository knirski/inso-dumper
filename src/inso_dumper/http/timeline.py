"""Timeline API: pure page parser and the paginated / media shells.

Functional core (``parse_timeline_page``): bytes in, models out, no IO.
The shell functions (``list_posts``, ``download_media``) own the
``HttpClient``, retries, and backoff sleeps, and yield ``CliResult``
items so consumers short-circuit on ``Err`` without any exception-based
control flow (AGENTS.md: exceptions are not for expected failures).

Endpoint (verified by the Aug 29 spike; see the spec's Findings):

    GET /system-api/timeline/posts?page={n}&categoryId={id}&child={uuid}

Read-only GETs only — the platform's ``PUT .../view`` is never called.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import HttpClient, Response
from inso_dumper.models.session import Session
from inso_dumper.models.timeline import Category, MediaItemKind, Post

# Retry policy for ``waitingToProcess > 0`` pages: exponential backoff
# capped at 30s, 3 retries, then give up (4 fetch attempts total).
_WAIT_RETRIES: Final = 3
_BACKOFF_CAP_SECONDS: Final = 30.0

# Test seam: patched in tests to avoid real sleeps.
_sleep: Final = asyncio.sleep


class TimelinePage(BaseModel):
    """One paginated response envelope."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[Post]
    waiting_to_process: int = Field(alias="waitingToProcess")


def _platform_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="timeline_page"))


def _parse_page(body: bytes) -> CliResult[TimelinePage]:
    """Parse one API response envelope, drift-fail on any unknown key."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return _platform_changed()
    try:
        return Ok(TimelinePage.model_validate(data))
    except ValidationError:
        return _platform_changed()


def parse_timeline_page(body: bytes) -> CliResult[list[Post]]:
    """Parse one API page into its posts.

    An empty ``items`` array is ``Ok([])`` — the iterator's stop signal.
    Malformed JSON or any unknown key (item or envelope) is
    ``Err(PLATFORM_CHANGED)``: the dumper fails loud rather than
    silently dropping fields.
    """
    match _parse_page(body):
        case Err(error):
            return Err(error)
        case Ok(page):
            return Ok(page.items)


async def list_posts(
    client: HttpClient,
    session: Session,
    child_uuid: str,
    category: Category,
) -> AsyncIterator[CliResult[Post]]:
    """Drive the page loop, yielding one ``Ok(post)`` per post.

    ``child_uuid`` is the UUID resolved from the children scrape —
    never ``session.user_uuid``, which is the parent's. Pages are
    fetched until the first empty ``items`` page; a page with
    ``waitingToProcess > 0`` is retried with exponential backoff
    (cap 30s, 3 retries) before giving up with ``Err(HTTP)``. A 401/403
    surfaces ``SESSION_EXPIRED`` (the saved PHPSESSID no longer works).

    Errors are yielded as ``Err`` and end the iteration; nothing here
    raises.
    """
    page_number = 1
    while True:
        path = (
            f"/system-api/timeline/posts?page={page_number}"
            f"&categoryId={category.api_id}&child={child_uuid}"
        )
        headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
        result = await client.request("GET", path, headers=headers)
        response: Response | None
        match result:
            case Err(error):
                yield Err(error)
                return
            case Ok(response):
                pass
        if response.status in (401, 403):
            yield Err(CliError(kind=CliErrorKind.SESSION_EXPIRED, subject="timeline_posts"))
            return
        if response.status != 200:
            yield Err(CliError(kind=CliErrorKind.HTTP, subject=f"status_{response.status}"))
            return
        page_or_err = _parse_page(response.body)
        match page_or_err:
            case Err(error):
                yield Err(error)
                return
            case Ok(page):
                pass
        if page.waiting_to_process > 0:
            for retry in range(_WAIT_RETRIES):
                await _sleep(min(_BACKOFF_CAP_SECONDS, 2.0**retry))
                result = await client.request("GET", path, headers=headers)
                match result:
                    case Err(error):
                        yield Err(error)
                        return
                    case Ok(response):
                        pass
                if response.status in (401, 403):
                    yield Err(CliError(kind=CliErrorKind.SESSION_EXPIRED, subject="timeline_posts"))
                    return
                if response.status != 200:
                    yield Err(CliError(kind=CliErrorKind.HTTP, subject=f"status_{response.status}"))
                    return
                page_or_err = _parse_page(response.body)
                match page_or_err:
                    case Err(error):
                        yield Err(error)
                        return
                    case Ok(page):
                        pass
                if page.waiting_to_process == 0:
                    break
            else:
                yield Err(CliError(kind=CliErrorKind.HTTP, subject="waiting_to_process"))
                return
        if not page.items:
            return
        for post in page.items:
            yield Ok(post)
        page_number += 1


async def download_media(
    client: HttpClient,
    post: Post,
) -> AsyncIterator[CliResult[tuple[MediaItemKind, str, bytes]]]:
    """Fetch each media file's bytes, yielding ``(kind, name, bytes)``.

    Photos come from ``src.full``, videos from ``src.full``, attachments
    from ``url``. Signed URLs are absolute (host ``file.inso.pl``); the
    full URL is passed as the request path and the ``Host`` header is
    set from it. Each file's bytes are read fully into memory before
    the yield (per-file buffering; no cross-file accumulation, no
    incremental hashing in v1 — the writer hashes the yielded bytes).

    Errors are yielded as ``Err(CliError(HTTP, 'media_download'))`` —
    or the client's Err unchanged — and end the iteration.
    """
    items: list[tuple[MediaItemKind, str, str]] = [
        *((MediaItemKind.PHOTO, p.name, str(p.src.full)) for p in post.media.photos),
        *((MediaItemKind.VIDEO, v.name, str(v.src.full)) for v in post.media.videos),
        *((MediaItemKind.ATTACHMENT, a.name, str(a.url)) for a in post.media.attachments),
    ]
    for kind, name, url in items:
        headers = {"Host": urlsplit(url).netloc}
        result = await client.request("GET", url, headers=headers)
        match result:
            case Err(error):
                yield Err(error)
                return
            case Ok(response):
                pass
        if response.status != 200:
            yield Err(CliError(kind=CliErrorKind.HTTP, subject="media_download"))
            return
        yield Ok((kind, name, response.body))


__all__ = ["MediaItemKind", "download_media", "list_posts", "parse_timeline_page"]
