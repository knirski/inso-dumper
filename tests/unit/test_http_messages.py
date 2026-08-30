"""Unit tests for the communicator parsers and shells (http/messages.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http.messages import (
    parse_conversations_page,
    parse_messages_page,
)
from tests.conftest import make_session

SPIKE_DIR = Path("docs/api-notes-data")
CONVERSATIONS_SAMPLE = SPIKE_DIR / "conversations-sample.json"
DETAIL_SAMPLE = SPIKE_DIR / "conversation-detail-sample.json"


def _conversations_body() -> bytes:
    if not CONVERSATIONS_SAMPLE.exists():
        pytest.skip(f"spike corpus absent: {CONVERSATIONS_SAMPLE}")
    return CONVERSATIONS_SAMPLE.read_bytes()


def _messages_body() -> bytes:
    if not DETAIL_SAMPLE.exists():
        pytest.skip(f"spike corpus absent: {DETAIL_SAMPLE}")
    return DETAIL_SAMPLE.read_bytes()


def _empty_conversations_body() -> bytes:
    return json.dumps(
        {
            "categories": [],
            "category": "main",
            "conversations": [],
            "templates": [],
            "unreadCount": 0,
        }
    ).encode()


# --- parse_conversations_page -------------------------------------------------


def test_parse_conversations_page_spike_capture() -> None:
    result = parse_conversations_page(_conversations_body())
    assert isinstance(result, Ok)
    assert len(result.value.conversations) == 2


def test_parse_conversations_page_empty_is_ok() -> None:
    result = parse_conversations_page(_empty_conversations_body())
    assert isinstance(result, Ok)
    assert result.value.conversations == []


def test_parse_conversations_page_unknown_key_is_platform_changed() -> None:
    data = json.loads(_conversations_body()) | {"brandNewKey": 1}
    result = parse_conversations_page(json.dumps(data).encode())
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "conversations_page"


def test_parse_conversations_page_malformed_json_is_platform_changed() -> None:
    result = parse_conversations_page(b"{not json")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "conversations_page"


def test_parse_conversations_page_non_utf8_is_platform_changed() -> None:
    result = parse_conversations_page(b"\xff\xfe\x00")
    assert isinstance(result, Err)
    assert result.error.subject == "conversations_page"


# --- parse_messages_page ------------------------------------------------------


def test_parse_messages_page_spike_capture_is_bare_list() -> None:
    result = parse_messages_page(_messages_body())
    assert isinstance(result, Ok)
    assert len(result.value) == 30
    assert all(m.id for m in result.value)


def test_parse_messages_page_empty_list_is_ok() -> None:
    result = parse_messages_page(b"[]")
    assert isinstance(result, Ok)
    assert result.value == []


def test_parse_messages_page_non_list_body_is_platform_changed() -> None:
    """The messages envelope is a BARE LIST; an object is drift."""
    result = parse_messages_page(b'{"messages": []}')
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "messages_page"


def test_parse_messages_page_unknown_key_is_platform_changed() -> None:
    data = json.loads(_messages_body())
    data[0]["brandNewKey"] = 1
    result = parse_messages_page(json.dumps(data).encode())
    assert isinstance(result, Err)
    assert result.error.subject == "messages_page"


def test_parse_messages_page_malformed_json_is_platform_changed() -> None:
    result = parse_messages_page(b"[not json")
    assert isinstance(result, Err)
    assert result.error.subject == "messages_page"


def test_parse_messages_page_non_utf8_is_platform_changed() -> None:
    result = parse_messages_page(b"\xff\xfe\x00")
    assert isinstance(result, Err)
    assert result.error.subject == "messages_page"


# --- shared: both parsers exported ---------------------------------------------


def test_parsers_exported() -> None:
    import inso_dumper.http.messages as mod

    assert "parse_conversations_page" in mod.__all__
    assert "parse_messages_page" in mod.__all__


# --- list_conversations shell (T4) ---------------------------------------------


def _response(status: int, body: bytes, headers: list[tuple[str, str]] | None = None) -> Any:
    from inso_dumper.http.client import Response

    return Ok(Response(status=status, body=body, headers=tuple(headers or ())))


def _conversation(id: str) -> dict[str, Any]:
    return {
        "id": id,
        "lastUpdate": 1787814351,
        "recipient": {"name": "Żółta", "description": "d", "type": "teachers", "prefix": "G:"},
        "read": True,
        "excerpt": "e",
        "branch": "b",
        "participantsNames": [],
    }


def _envelope(conversations: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        {
            "categories": [],
            "category": "main",
            "conversations": conversations,
            "templates": [],
            "unreadCount": 0,
        }
    ).encode()


def test_list_conversations_two_page_walk() -> None:
    from inso_dumper.http.messages import list_conversations

    client = FakeHttpClientScript(
        [
            _response(200, _envelope([_conversation("a"), _conversation("b")])),
            _response(200, _envelope([_conversation("c"), _conversation("d"), _conversation("e")])),
            _response(200, _envelope([])),
        ]
    )
    session = make_session()

    async def _run() -> list[str]:
        seen: list[str] = []
        async for item in list_conversations(client, session):
            match item:
                case Err(error):
                    raise AssertionError(f"unexpected error: {error}")
                case Ok(conversation):
                    seen.append(conversation.id)
        return seen

    seen = asyncio.run(_run())
    assert seen == ["a", "b", "c", "d", "e"]
    assert client.calls[0]["path"] == "/system-api/conversations?category=main"
    assert client.calls[1]["path"] == "/system-api/conversations?category=main&loadOlder=1"
    assert client.calls[2]["path"] == "/system-api/conversations?category=main&loadOlder=1"
    headers = client.calls[0].get("headers") or {}
    assert "PHPSESSID=" in headers.get("Cookie", "")


def test_list_conversations_empty_first_page_stops_after_one_request() -> None:
    from inso_dumper.http.messages import list_conversations

    client = FakeHttpClientScript([_response(200, _envelope([]))])
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in list_conversations(client, session)]

    items = asyncio.run(_run())
    assert items == []
    assert len(client.calls) == 1


def test_list_conversations_non_200_is_http_error() -> None:
    from inso_dumper.http.messages import list_conversations

    client = FakeHttpClientScript([_response(500, b"boom")])
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in list_conversations(client, session)]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.kind is CliErrorKind.HTTP
    assert items[0].error.subject == "conversations_status_500"


def test_list_conversations_client_err_propagates_and_ends_iteration() -> None:
    from inso_dumper.http.messages import list_conversations

    err = Err(CliError(kind=CliErrorKind.HTTP, subject="connect"))
    client = FakeHttpClientScript([err, _response(200, _envelope([_conversation("a")]))])
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in list_conversations(client, session)]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.subject == "connect"
    assert len(client.calls) == 1  # iteration ended, no second request


def test_list_conversations_ignoring_loadolder_terminates() -> None:
    """No-progress guard: a server that re-serves the same first page
    forever must not loop; the walk stops after the repeat page."""
    from inso_dumper.http.messages import list_conversations

    same_page = _envelope([_conversation("a")])
    client = FakeHttpClientScript([_response(200, same_page) for _ in range(10)])
    session = make_session()

    async def _run() -> list[str]:
        seen: list[str] = []
        async for item in list_conversations(client, session):
            match item:
                case Err(error):
                    raise AssertionError(f"unexpected error: {error}")
                case Ok(conversation):
                    seen.append(conversation.id)
        return seen

    seen = asyncio.run(_run())
    assert seen == ["a"]  # yielded once; the repeat page ends the walk
    assert len(client.calls) == 2  # first page + one repeat, then stop


def test_list_conversations_never_calls_forbidden_endpoints() -> None:
    from inso_dumper.http.messages import list_conversations

    client = FakeHttpClientScript(
        [
            _response(200, _envelope([_conversation("a")])),
            _response(200, _envelope([])),
        ]
    )
    session = make_session()

    async def _run() -> None:
        async for item in list_conversations(client, session):
            assert isinstance(item, Ok)

    asyncio.run(_run())
    for call in client.calls:
        assert "read-all-unread" not in call["path"]


class FakeHttpClientScript:
    """Script-based fake client for shell tests (calls recorded)."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Any = None,
        headers: Any = None,
    ) -> Any:
        self.calls.append({"method": method, "path": path, "form": form, "headers": headers})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        pass


# --- fetch_messages shell (T5) -------------------------------------------------


def _message(id: str) -> dict[str, Any]:
    return {
        "id": id,
        "message": "hello",
        "sendDate": "czwartek,  9:05",
        "sendTimestamp": 1787814351,
        "sender": {"type": "worker", "name": "A", "initials": "A", "avatar": None},
        "incoming": True,
        "attachments": [],
        "main": False,
        "isRemoved": False,
        "canRemove": False,
    }


def _message_page(count: int, offset: int = 0) -> bytes:
    return json.dumps([_message(f"m{offset + i}") for i in range(count)]).encode()


def test_fetch_messages_walk_requests_and_final_empty_page() -> None:
    from inso_dumper.http.messages import fetch_messages

    client = FakeHttpClientScript(
        [
            _response(200, _message_page(30, 0)),
            _response(200, _message_page(30, 30)),
            _response(200, _message_page(12, 60)),
            _response(200, b"[]"),
        ]
    )
    session = make_session()

    async def _run() -> list[Any]:
        pages: list[Any] = []
        async for item in fetch_messages(client, session, "conv-1"):
            pages.append(item)
        return pages

    pages = asyncio.run(_run())
    assert all(isinstance(p, Ok) for p in pages)
    assert [len(p.value) for p in pages] == [30, 30, 12]
    expected = [
        "/system-api/conversations/conv-1?messages=1&startingIndex=0",
        "/system-api/conversations/conv-1?messages=1&startingIndex=30",
        "/system-api/conversations/conv-1?messages=1&startingIndex=60",
        "/system-api/conversations/conv-1?messages=1&startingIndex=72",
    ]
    assert [c["path"] for c in client.calls] == expected


def test_fetch_messages_short_page_still_requests_empty_tail() -> None:
    from inso_dumper.http.messages import fetch_messages

    client = FakeHttpClientScript(
        [
            _response(200, _message_page(5)),
            _response(200, b"[]"),
        ]
    )
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in fetch_messages(client, session, "c")]

    pages = asyncio.run(_run())
    assert [len(p.value) for p in pages if isinstance(p, Ok)] == [5]
    assert len(client.calls) == 2
    assert client.calls[1]["path"] == "/system-api/conversations/c?messages=1&startingIndex=5"


def test_fetch_messages_non_200_is_http_error() -> None:
    from inso_dumper.http.messages import fetch_messages

    client = FakeHttpClientScript([_response(403, b"no")])
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in fetch_messages(client, session, "c")]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.subject == "messages_status_403"


def test_fetch_messages_client_err_propagates() -> None:
    from inso_dumper.http.messages import fetch_messages

    err = Err(CliError(kind=CliErrorKind.HTTP, subject="timeout"))
    client = FakeHttpClientScript([err])
    session = make_session()

    async def _run() -> list[Any]:
        return [item async for item in fetch_messages(client, session, "c")]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.subject == "timeout"
