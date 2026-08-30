"""Communicator API: pure page parsers and the paginated shells.

Functional core (``parse_conversations_page``, ``parse_messages_page``):
bytes in, models out, no IO. The shells (``list_conversations``,
``fetch_messages``) own the ``HttpClient`` and yield ``CliResult``
items so consumers short-circuit on ``Err`` without any exception-based
control flow (AGENTS.md: exceptions are not for expected failures).

Endpoints (verified by the Aug 2026 spike; see the spec's Findings):

    GET /system-api/conversations?category=main[&loadOlder=1]
    GET /system-api/conversations/{id}?messages=1[&startingIndex=N]

The conversations envelope is an object; the messages envelope is a
BARE LIST (30 items per page, one request past the end for a clean
stop). Read-only GETs only — ``POST .../read-all-unread`` and
``POST /system-api/messages/attachment`` are never called.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import HttpClient
from inso_dumper.models.messages import Conversation, ConversationListPage, Message
from inso_dumper.models.session import Session

_CONVERSATIONS_PATH = "/system-api/conversations"


def _conversations_platform_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="conversations_page"))


def _messages_platform_changed() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="messages_page"))


def parse_conversations_page(body: bytes) -> CliResult[ConversationListPage]:
    """Parse one conversations envelope, drift-fail on any unknown key.

    Malformed JSON (including non-UTF-8 bytes) or any unknown key
    (envelope or item) is ``Err(PLATFORM_CHANGED)``: the dumper fails
    loud rather than silently dropping fields.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _conversations_platform_changed()
    try:
        return Ok(ConversationListPage.model_validate(data))
    except ValidationError:
        return _conversations_platform_changed()


def parse_messages_page(body: bytes) -> CliResult[list[Message]]:
    """Parse one ``?messages=1`` page — a bare JSON list of messages.

    An empty list is ``Ok([])`` — the iterator's stop signal. A body
    that is not a list (the envelope shape), malformed JSON, or any
    unknown key is ``Err(PLATFORM_CHANGED)``.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _messages_platform_changed()
    if not isinstance(data, list):
        return _messages_platform_changed()
    try:
        return Ok([Message.model_validate(item) for item in data])
    except ValidationError:
        return _messages_platform_changed()


async def list_conversations(
    client: HttpClient, session: Session
) -> AsyncIterator[CliResult[Conversation]]:
    """Walk the account's conversation list, one ``Ok`` per conversation.

    The first page is ``?category=main``; subsequent pages add
    ``&loadOlder=1``. The walk stops on an empty page or on a
    no-progress page (only already-seen ids — protects against a server
    ignoring ``loadOlder``). Non-200 surfaces
    ``Err(HTTP, 'conversations_status_<n>')``; transport errors are
    propagated unchanged and end the iteration.
    """
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    seen_ids: set[str] = set()
    load_older = False
    while True:
        path = f"{_CONVERSATIONS_PATH}?category=main"
        if load_older:
            path += "&loadOlder=1"
        result = await client.request("GET", path, headers=headers)
        response = None
        match result:
            case Err(error):
                yield Err(error)
                return
            case Ok(value):
                response = value
        if response.status != 200:
            yield Err(
                CliError(
                    kind=CliErrorKind.HTTP, subject=f"conversations_status_{response.status}"
                )
            )
            return
        page_or_err = parse_conversations_page(response.body)
        match page_or_err:
            case Err(error):
                yield Err(error)
                return
            case Ok(page):
                pass
        if not page.conversations:
            return
        progressed = False
        for conversation in page.conversations:
            if conversation.id in seen_ids:
                continue
            seen_ids.add(conversation.id)
            progressed = True
            yield Ok(conversation)
        if not progressed:
            # The server re-served only known conversations (e.g. it
            # ignored loadOlder): no new pages are coming.
            return
        load_older = True


async def fetch_messages(
    client: HttpClient,
    session: Session,
    conversation_id: str,
    starting_index: int = 0,
) -> AsyncIterator[CliResult[list[Message]]]:
    """Walk one conversation's history in pages from ``starting_index``.

    Yields each parsed page as a list; advances ``startingIndex`` by the
    page length; stops when a page yields zero messages (one request
    past the end — the server page size is not assumed). The consumer
    dedupes by message id, so boundary overlap is harmless. Non-200
    surfaces ``Err(HTTP, 'messages_status_<n>')``.
    """
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    offset = starting_index
    while True:
        path = f"{_CONVERSATIONS_PATH}/{conversation_id}?messages=1&startingIndex={offset}"
        result = await client.request("GET", path, headers=headers)
        response = None
        match result:
            case Err(error):
                yield Err(error)
                return
            case Ok(value):
                response = value
        if response.status != 200:
            yield Err(
                CliError(kind=CliErrorKind.HTTP, subject=f"messages_status_{response.status}")
            )
            return
        page_or_err = parse_messages_page(response.body)
        match page_or_err:
            case Err(error):
                yield Err(error)
                return
            case Ok(page):
                pass
        if not page:
            return
        offset += len(page)
        yield Ok(page)


__all__ = [
    "fetch_messages",
    "list_conversations",
    "parse_conversations_page",
    "parse_messages_page",
]
