"""Unit tests for the timeline HTTP shell: list_posts and download_media."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http import timeline as timeline_module
from inso_dumper.http.timeline import download_media, list_posts
from inso_dumper.models.session import Session
from inso_dumper.models.timeline import Category, Post
from tests.conftest import (
    FakeHttpClient,
)
from tests.conftest import (
    make_attachment_dict as _attachment,
)
from tests.conftest import (
    make_photo_dict as _photo,
)
from tests.conftest import (
    make_post_dict as _post_dict,
)
from tests.conftest import (
    make_video_dict as _video,
)

SPIKE_DIR = Path("docs/api-notes-data")
CHILD_UUID = "0123abcd-1111-2222-3333-444455556666"


def _spike_items(fname: str) -> list[dict[str, Any]]:
    path = SPIKE_DIR / fname
    if not path.exists():
        pytest.skip(f"spike corpus absent: {path}")
    return json.loads(path.read_bytes())["items"]


def _page(
    items: list[dict[str, Any]], waiting: int = 0
) -> tuple[int, bytes, list[tuple[str, str]]]:
    body = json.dumps({"items": items, "waitingToProcess": waiting}).encode("utf-8")
    return (200, body, [])


def _make_post(**media: Any) -> Post:
    payload = _post_dict()
    payload["media"].update(media)
    return Post.model_validate(payload)


async def _collect(
    aiter: AsyncIterator[Ok[Post] | Err[CliError]]
    | AsyncIterator[Ok[tuple[Any, str, bytes]] | Err[CliError]],
) -> list[Ok[Post] | Err[CliError] | Ok[tuple[Any, str, bytes]]]:
    out: list[Ok[Post] | Err[CliError] | Ok[tuple[Any, str, bytes]]] = []
    async for item in aiter:
        out.append(item)
    return out


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the retry backoff sleep so waiting-retry tests are instant."""

    async def _instant(_seconds: float) -> None:
        pass

    monkeypatch.setattr(timeline_module, "_sleep", _instant)


# --- list_posts --------------------------------------------------------------


def test_list_posts_single_page_yields_ten_and_stops() -> None:
    items = _spike_items("timeline-posts-page1-category2.json")
    # One extra request past the end: a full page cannot be known to be
    # last, so the iterator always probes the next page (design stop rule).
    client = FakeHttpClient([_page(items), _page([])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 10
    assert all(isinstance(r, Ok) for r in results)
    assert all(isinstance(r.value, Post) for r in results if isinstance(r, Ok))
    assert len(client.calls) == 2
    expected_path = f"/system-api/timeline/posts?page=1&categoryId=2&child={CHILD_UUID}"
    assert client.calls[0]["path"] == expected_path


def test_list_posts_uses_child_uuid_not_session_user_uuid() -> None:
    """The child= param must carry the children-scrape UUID, never the
    parent's session.user_uuid (design gotcha 'Child UUID source')."""
    items = _spike_items("timeline-posts-page1-category2.json")
    client = FakeHttpClient([_page(items), (200, b'{"items": [], "waitingToProcess": 0}', [])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    for call in client.calls:
        assert "parent-uuid" not in call["path"]
        assert CHILD_UUID in call["path"]


def test_list_posts_sends_phpsessid_cookie() -> None:
    items = _spike_items("timeline-posts-page1-category2.json")
    client = FakeHttpClient([_page(items), (200, b'{"items": [], "waitingToProcess": 0}', [])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    headers = client.calls[0].get("headers") or {}
    assert headers.get("Cookie") == "PHPSESSID=s3ss10n"


def test_list_posts_galleries_category_id() -> None:
    client = FakeHttpClient([_page([])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.GALLERIES)))  # type: ignore[arg-type]

    assert results == []
    assert "categoryId=3" in client.calls[0]["path"]


def test_list_posts_two_pages_plus_empty_stop_page() -> None:
    """Stop condition per design: stop on the first empty items page —
    a full 10-item page cannot be known to be last, so 10+10 posts cost
    three GETs (one extra request past the end)."""
    items = _spike_items("timeline-posts-page1-category2.json")
    client = FakeHttpClient([_page(items), _page(items), _page([])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 20
    assert len(client.calls) == 3
    assert "page=2" in client.calls[1]["path"]


def test_list_posts_short_last_page_yields_all_and_stops_on_empty() -> None:
    items = _spike_items("timeline-posts-page1-category2.json")[:4]
    client = FakeHttpClient([_page(items), _page([])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 4
    assert len(client.calls) == 2


def test_list_posts_waiting_page_is_retried_not_yielded() -> None:
    items = _spike_items("timeline-posts-page1-category2.json")
    client = FakeHttpClient([_page([], waiting=1), _page(items), _page([])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 10
    assert len(client.calls) == 3
    # The retry re-requests the same page.
    assert client.calls[0]["path"] == client.calls[1]["path"]


def test_list_posts_waiting_on_all_retries_surfaces_http_err() -> None:
    client = FakeHttpClient([_page([], waiting=1)] * 4)
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(client.calls) == 4
    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert results[0].error.kind is CliErrorKind.HTTP
    assert results[0].error.subject == "waiting_to_process"


def test_list_posts_client_err_propagated_unchanged() -> None:
    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    client = FakeHttpClient([Err(err)])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert results == [Err(err)]


def test_list_posts_unauthorized_surfaces_session_expired() -> None:
    """Design: a 401/403 during sync surfaces SESSION_EXPIRED (exit 4)."""
    client = FakeHttpClient([(401, b"unauthorized", [])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert results[0].error.kind is CliErrorKind.SESSION_EXPIRED


def test_list_posts_other_error_status_surfaces_http() -> None:
    client = FakeHttpClient([(500, b"boom", [])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert results[0].error.kind is CliErrorKind.HTTP


def test_list_posts_parser_drift_surfaces_platform_changed() -> None:
    body = json.dumps({"items": [], "waitingToProcess": 0, "surprise": 1}).encode("utf-8")
    client = FakeHttpClient([(200, body, [])])
    session = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")

    results = asyncio.run(_collect(list_posts(client, session, CHILD_UUID, Category.ANNOUNCEMENTS)))  # type: ignore[arg-type]

    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert results[0].error.kind is CliErrorKind.PLATFORM_CHANGED


# --- download_media ----------------------------------------------------------


def test_download_media_photo_yields_bytes_with_kind_and_name() -> None:
    post = _make_post(photos=[_photo()])
    client = FakeHttpClient([(200, b"jpeg-bytes", [])])

    results = asyncio.run(_collect(download_media(client, post)))

    assert len(results) == 1
    assert isinstance(results[0], Ok)
    kind, name, data = results[0].value
    assert kind is timeline_module.MediaItemKind.PHOTO
    assert name == "IMG_1.jpeg"
    assert data == b"jpeg-bytes"
    assert client.calls[0]["path"].startswith("https://file.inso.pl/")
    headers = client.calls[0].get("headers") or {}
    assert headers.get("Host") == "file.inso.pl"


def test_download_media_attachment_uses_url() -> None:
    post = _make_post(attachments=[_attachment()])
    client = FakeHttpClient([(200, b"pdf-bytes", [])])

    results = asyncio.run(_collect(download_media(client, post)))

    assert isinstance(results[0], Ok)
    kind, name, data = results[0].value
    assert kind is timeline_module.MediaItemKind.ATTACHMENT
    assert name == "dokument.pdf"
    assert data == b"pdf-bytes"
    assert client.calls[0]["path"] == _attachment()["url"]


def test_download_media_video_yields_video_kind() -> None:
    post = _make_post(photos=[_video()])
    client = FakeHttpClient([(200, b"mp4-bytes", [])])

    results = asyncio.run(_collect(download_media(client, post)))

    assert isinstance(results[0], Ok)
    kind, name, data = results[0].value
    assert kind is timeline_module.MediaItemKind.VIDEO
    assert name == "klip.mp4"
    assert data == b"mp4-bytes"


def test_download_media_three_photos_three_calls_in_order() -> None:
    post = _make_post(photos=[_photo("a.jpeg"), _photo("b.jpeg"), _photo("c.jpeg")])
    client = FakeHttpClient([(200, b"x", []), (200, b"y", []), (200, b"z", [])])

    results = asyncio.run(_collect(download_media(client, post)))

    assert [r.value[2] for r in results if isinstance(r, Ok)] == [b"x", b"y", b"z"]
    assert len(client.calls) == 3


def test_download_media_error_mid_stream_propagates() -> None:
    post = _make_post(photos=[_photo("a.jpeg"), _photo("b.jpeg")])
    err = CliError(kind=CliErrorKind.HTTP, subject="timeout")
    client = FakeHttpClient([(200, b"first", []), Err(err)])

    results = asyncio.run(_collect(download_media(client, post)))

    assert len(results) == 2
    assert isinstance(results[0], Ok)
    assert isinstance(results[1], Err)
    assert results[1].error is err


def test_download_media_error_status_surfaces_http_media_download() -> None:
    post = _make_post(photos=[_photo()])
    client = FakeHttpClient([(403, b"expired", [])])

    results = asyncio.run(_collect(download_media(client, post)))

    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert results[0].error.kind is CliErrorKind.HTTP
    assert results[0].error.subject == "media_download"


def test_download_media_no_media_makes_no_calls() -> None:
    post = _make_post()
    client = FakeHttpClient([])

    results = asyncio.run(_collect(download_media(client, post)))

    assert results == []
    assert client.calls == []
