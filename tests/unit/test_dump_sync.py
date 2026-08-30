"""Unit tests for the dump sync orchestrator (dump/sync.py)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump import sync as sync_module
from inso_dumper.dump.layout import derive_post_slug
from inso_dumper.dump.manifest import open_manifest
from inso_dumper.dump.sync import run, run_documents, run_messages
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http import timeline as timeline_module
from inso_dumper.models.children import Child
from inso_dumper.models.session import Session
from inso_dumper.models.timeline import Category, Post
from tests.conftest import FakeHttpClient, make_photo_dict, make_post_dict

LOG = logging.getLogger("test")

CHILD = Child(
    child_id="0123abcd-1111-2222-3333-444455556666",
    first_name="Franek",
    last_name="Kowalski",
    group=None,
    avatar_color="#FCCC34",
    initials="FK",
)
SESSION = Session(phpsessid="s3ss10n", user_uuid="parent-uuid")


def _spike_items(fname: str) -> list[dict[str, Any]]:
    path = Path("docs/api-notes-data") / fname
    if not path.exists():
        pytest.skip(f"spike corpus absent: {path}")
    return json.loads(path.read_bytes())["items"]


def _page(
    items: list[dict[str, Any]], waiting: int = 0
) -> tuple[int, bytes, list[tuple[str, str]]]:
    body = json.dumps({"items": items, "waitingToProcess": waiting}).encode("utf-8")
    return (200, body, [])


def _make_post(post_id: str | None = None, **media: Any) -> Post:
    payload = make_post_dict()
    if post_id is not None:
        payload["id"] = post_id
    payload["media"].update(media)
    return Post.model_validate(payload)


def _post_dicts(posts: list[Post]) -> list[dict[str, Any]]:
    return [json.loads(p.model_dump_json()) for p in posts]


async def _collect(
    aiter: AsyncIterator[Ok[Post] | Err[CliError]],
) -> list[Ok[Post] | Err[CliError]]:
    return [item async for item in aiter]


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        pass

    monkeypatch.setattr(timeline_module, "_sleep", _instant)


def _run(client: FakeHttpClient, tmp_path: Path, **kwargs: Any) -> Ok[Any] | Err[CliError]:
    return asyncio.run(
        run(
            client=client,  # type: ignore[arg-type]
            session=SESSION,
            child=CHILD,
            category=Category.ANNOUNCEMENTS,
            dump_root=tmp_path,
            log=LOG,
            **kwargs,
        )
    )


def test_one_post_no_media_creates_post_files(tmp_path: Path) -> None:
    posts = [_make_post()]
    client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])

    result = _run(client, tmp_path)

    assert isinstance(result, Ok)
    summary = result.value
    assert (
        summary.posts_new,
        summary.posts_skipped,
        summary.photos,
        summary.videos,
        summary.attachments,
    ) == (1, 0, 0, 0, 0)
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    assert (post_dir / "post.json").is_file()
    assert (post_dir / "post.html").is_file()
    # manifest written under announcements/
    assert (tmp_path / "franek-k" / "announcements" / ".manifest.sqlite").is_file()


def test_one_post_with_photos_dedupes_and_links(tmp_path: Path) -> None:
    posts = [_make_post(photos=[make_photo_dict("a.jpeg"), make_photo_dict("b.jpeg")])]
    client = FakeHttpClient(
        [_page(_post_dicts(posts)), (200, b"jpeg-a", []), (200, b"jpeg-b", []), _page([])]
    )

    result = _run(client, tmp_path)

    assert isinstance(result, Ok)
    assert result.value.photos == 2
    common = tmp_path / "_common" / "photos"
    assert len(list(common.rglob("*.jpeg"))) == 2
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    links = list((post_dir / "photos").iterdir())
    assert len(links) == 2
    assert all(
        link.is_symlink() and link.resolve().is_relative_to(common.resolve()) for link in links
    )


def test_one_post_with_video_counts_videos(tmp_path: Path) -> None:
    post = _make_post(
        photos=[
            {
                "type": "video",
                "name": "klip.mp4",
                "src": {
                    "mp4": "https://file.inso.pl/t/1/video.mp4",
                    "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                },
            }
        ]
    )
    client = FakeHttpClient([_page(_post_dicts([post])), (200, b"mp4-bytes", []), _page([])])

    result = _run(client, tmp_path)

    assert isinstance(result, Ok)
    assert result.value.videos == 1
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(post)
    assert (post_dir / "videos" / "1.mp4").exists()
    assert len(list((tmp_path / "_common" / "videos").rglob("*.mp4"))) == 1


def test_second_run_skips_and_touches_nothing(tmp_path: Path) -> None:
    posts = [_make_post(photos=[make_photo_dict()])]
    pages = [_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])]
    first_client = FakeHttpClient(list(pages))
    first = _run(first_client, tmp_path)
    assert isinstance(first, Ok)

    before = sorted(str(p) for p in tmp_path.rglob("*"))
    second_client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    second = _run(second_client, tmp_path)

    assert isinstance(second, Ok)
    assert (second.value.posts_new, second.value.posts_skipped) == (0, 1)
    after = sorted(str(p) for p in tmp_path.rglob("*"))
    assert before == after


def test_media_failure_then_rerun_reuses_directory(tmp_path: Path) -> None:
    """A partial dump (post.json written, media failed, manifest not
    upserted) is re-dumped in place on the next run — no -1 orphan."""
    posts = [_make_post(photos=[make_photo_dict()])]
    failing = FakeHttpClient(
        [_page(_post_dicts(posts)), Err(CliError(kind=CliErrorKind.HTTP, subject="timeout"))]
    )
    failed = _run(failing, tmp_path)

    assert isinstance(failed, Err)
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    assert post_dir.is_dir()  # partial dump exists on disk

    ok_client = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])])
    recovered = _run(ok_client, tmp_path)

    assert isinstance(recovered, Ok)
    assert recovered.value.posts_new == 1
    assert (post_dir / "photos" / "1.jpeg").exists()
    orphans = [
        p
        for p in (tmp_path / "franek-k" / "announcements").iterdir()
        if p.is_dir() and p.name != derive_post_slug(posts[0])
    ]
    assert orphans == []


def test_slug_collision_gets_suffix(tmp_path: Path) -> None:
    first = _make_post(post_id="11111111-1111-1111-1111-111111111111")
    second = _make_post(post_id="22222222-2222-2222-2222-222222222222")
    client = FakeHttpClient([_page(_post_dicts([first, second])), _page([])])

    result = _run(client, tmp_path)

    assert isinstance(result, Ok)
    announcements = tmp_path / "franek-k" / "announcements"
    names = sorted(p.name for p in announcements.iterdir() if p.is_dir())
    assert names == [derive_post_slug(first), derive_post_slug(first) + "-1"]


def test_collision_suffixed_post_is_skipped_on_rerun(tmp_path: Path) -> None:
    """The manifest records the actual (suffixed) directory; a post dumped
    at <slug>-1 must skip on re-runs, not re-download."""
    first = _make_post(post_id="11111111-1111-1111-1111-111111111111")
    second = _make_post(post_id="22222222-2222-2222-2222-222222222222")
    client = FakeHttpClient([_page(_post_dicts([first, second])), _page([])])
    _run(client, tmp_path)

    rerun = FakeHttpClient([_page(_post_dicts([first, second])), _page([])])
    result = _run(rerun, tmp_path)

    assert isinstance(result, Ok)
    assert (result.value.posts_new, result.value.posts_skipped) == (0, 2)


def test_interrupted_force_rerun_recovers(tmp_path: Path) -> None:
    """An interrupted --force re-dump must not be mistaken for a complete
    dump later: force_clear drops the row up front, so the next normal
    run re-dumps via the partial-recovery path."""
    posts = [_make_post(photos=[make_photo_dict()])]
    setup = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])])
    _run(setup, tmp_path)

    forced_failing = FakeHttpClient(
        [_page(_post_dicts(posts)), Err(CliError(kind=CliErrorKind.HTTP, subject="timeout"))]
    )
    failed = _run(forced_failing, tmp_path, force={derive_post_slug(posts[0])})
    assert isinstance(failed, Err)

    recovery = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])])
    result = _run(recovery, tmp_path)

    assert isinstance(result, Ok)
    assert (result.value.posts_new, result.value.posts_skipped) == (1, 0)
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    assert (post_dir / "photos" / "1.jpeg").exists()


def test_dump_tree_modes_are_enforced(tmp_path: Path) -> None:
    """0o700 dirs (including intermediates) and a 0o600 manifest file."""
    posts = [_make_post(photos=[make_photo_dict()])]
    client = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])])

    result = _run(client, tmp_path)

    assert isinstance(result, Ok)
    child_dir = tmp_path / "franek-k"
    announcements = child_dir / "announcements"
    common = tmp_path / "_common"
    for d in (child_dir, announcements, common, common / "photos"):
        assert d.stat().st_mode & 0o777 == 0o700, d
    manifest = announcements / ".manifest.sqlite"
    assert manifest.stat().st_mode & 0o777 == 0o600


def test_manifest_entry_without_directory_is_redumped(tmp_path: Path) -> None:
    """Design: skip requires BOTH the manifest entry and the on-disk dir."""
    posts = [_make_post()]
    client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    first = _run(client, tmp_path)
    assert isinstance(first, Ok)

    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    shutil.rmtree(post_dir)

    again = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    result = _run(again, tmp_path)

    assert isinstance(result, Ok)
    assert result.value.posts_new == 1
    assert post_dir.is_dir()


def test_force_flag_redownloads_a_skipped_post(tmp_path: Path) -> None:
    posts = [_make_post(photos=[make_photo_dict()])]
    client = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg", []), _page([])])
    _run(client, tmp_path)

    forced = FakeHttpClient([_page(_post_dicts(posts)), (200, b"jpeg-new", []), _page([])])
    result = _run(forced, tmp_path, force={derive_post_slug(posts[0])})

    assert isinstance(result, Ok)
    assert (result.value.posts_new, result.value.posts_skipped) == (1, 0)
    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    assert (post_dir / "photos" / "1.jpeg").resolve().read_bytes() == b"jpeg-new"


def test_write_post_err_short_circuits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posts = [_make_post()]
    client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    failing = Err(CliError(kind=CliErrorKind.INTERNAL, subject="post_write"))
    monkeypatch.setattr(sync_module, "write_post", lambda *a, **k: failing)

    result = _run(client, tmp_path)

    assert isinstance(result, Err)
    assert result.error.subject == "post_write"


def test_child_uuid_used_in_request_paths(tmp_path: Path) -> None:
    posts = [_make_post()]
    client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    _run(client, tmp_path)
    for call in client.calls:
        assert "parent-uuid" not in call["path"]
        assert CHILD.child_id in call["path"]


# --- run_messages / run_documents (T9) ------------------------------------------

CONV_A_ID = "5b8789d6-4b05-11ed-9234-06f545343a70"
CONV_B_ID = "6b8789d6-4b05-11ed-9234-06f545343a70"


def _conv(id: str, last_update: int = 1787814351, name: str = "Żółta") -> dict[str, Any]:
    return {
        "id": id,
        "lastUpdate": last_update,
        "recipient": {"name": name, "description": "Wychowawcy", "type": "teachers", "prefix": "Grupa:"},
        "read": True,
        "excerpt": "e",
        "branch": "b",
        "participantsNames": [],
    }


def _msg(id: str, with_attachment: bool = False) -> dict[str, Any]:
    attachments: list[Any] | dict[str, Any] = []
    if with_attachment:
        attachments = {
            "media": [
                {
                    "name": "IMG_8171.jpeg",
                    "url": {
                        "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
                        "full": "https://file.inso.pl/t/1/full.jpg?Expires=1",
                    },
                    "isVideo": False,
                }
            ],
            "other": [],
        }
    return {
        "id": id,
        "message": "hello",
        "sendDate": "czwartek,  9:05",
        "sendTimestamp": 1787814351,
        "sender": {"type": "worker", "name": "A", "initials": "A", "avatar": None},
        "incoming": True,
        "attachments": attachments,
        "main": False,
        "isRemoved": False,
        "canRemove": False,
    }


def _conv_page(convs: list[dict[str, Any]]) -> tuple[int, bytes, list[tuple[str, str]]]:
    body = json.dumps(
        {"categories": [], "category": "main", "conversations": convs, "templates": [], "unreadCount": 0}
    ).encode()
    return (200, body, [])


def _msg_page(msgs: list[dict[str, Any]]) -> tuple[int, bytes, list[tuple[str, str]]]:
    return (200, json.dumps(msgs).encode(), [])


def _open_messages_manifest(tmp_path: Path) -> Any:
    result = open_manifest(tmp_path / "messages" / ".manifest.sqlite")
    assert isinstance(result, Ok)
    return result.value


def _run_messages(client: FakeHttpClient, tmp_path: Path, **kwargs: Any) -> Ok[Any] | Err[CliError]:
    return asyncio.run(
        run_messages(
            client=client,  # type: ignore[arg-type]
            session=SESSION,
            dump_root=tmp_path,
            log=LOG,
            **kwargs,
        )
    )


def test_run_messages_full_first_run(tmp_path: Path) -> None:
    client = FakeHttpClient(
        [
            _conv_page([_conv(CONV_A_ID), _conv(CONV_B_ID)]),
            _msg_page([_msg("a1")]),
            _msg_page([]),
            _msg_page([_msg("b1", with_attachment=True)]),
            _msg_page([]),
            (200, b"attachment-bytes", []),
            _conv_page([]),
        ]
    )

    result = _run_messages(client, tmp_path)

    assert isinstance(result, Ok)
    summary = result.value
    assert (summary.conversations, summary.messages, summary.message_attachments) == (2, 2, 1)

    messages_dir = tmp_path / "messages"
    manifest = _open_messages_manifest(tmp_path)
    try:
        assert manifest.recorded_conversation(CONV_A_ID) is not None
        recorded_b = manifest.recorded_conversation(CONV_B_ID)
        assert recorded_b is not None and recorded_b.message_count == 1
    finally:
        manifest.close()
    conv_b_dir = messages_dir / recorded_b.dir_name
    assert (conv_b_dir / "conversation.json").is_file()
    assert (conv_b_dir / "messages.json").is_file()
    assert (conv_b_dir / "attachments" / "b1" / "1.jpeg").exists()
    assert len(list((tmp_path / "_common" / "photos").rglob("*.jpeg"))) == 1
    # forbidden endpoints never requested
    for call in client.calls:
        assert "read-all-unread" not in call["path"]
        assert "messages/attachment" not in call["path"].replace("/system-api/conversations", "")
        assert not call["path"].startswith(("/drive/directory", "/drive/file"))


def test_run_messages_second_run_skips_everything(tmp_path: Path) -> None:
    page = _conv_page([_conv(CONV_A_ID)])
    setup = FakeHttpClient([page, _msg_page([_msg("a1")]), _msg_page([]), _conv_page([])])
    first = _run_messages(setup, tmp_path)
    assert isinstance(first, Ok)

    rerun = FakeHttpClient([page, _conv_page([])])
    result = _run_messages(rerun, tmp_path)

    assert isinstance(result, Ok)
    assert (result.value.conversations, result.value.messages) == (0, 0)
    # only list-page requests: no conversation history was fetched
    assert all(c["path"].startswith("/system-api/conversations?") for c in rerun.calls)


def test_run_messages_tail_fetch_uses_recorded_count(tmp_path: Path) -> None:
    setup = FakeHttpClient(
        [
            _conv_page([_conv(CONV_A_ID)]),
            _msg_page([_msg("a1")]),
            _msg_page([]),
            _conv_page([]),
        ]
    )
    first = _run_messages(setup, tmp_path)
    assert isinstance(first, Ok)

    # a new message arrives; lastUpdate changes
    changed = _conv_page([_conv(CONV_A_ID, last_update=1787900751)])
    tail = FakeHttpClient(
        [changed, _msg_page([_msg("a2")]), _msg_page([]), _conv_page([])]
    )
    result = _run_messages(tail, tmp_path)

    assert isinstance(result, Ok)
    assert (result.value.conversations, result.value.messages) == (1, 1)
    assert tail.calls[1]["path"] == (
        f"/system-api/conversations/{CONV_A_ID}?messages=1&startingIndex=1"
    )
    manifest = _open_messages_manifest(tmp_path)
    try:
        recorded = manifest.recorded_conversation(CONV_A_ID)
        assert recorded is not None and recorded.message_count == 2
        assert recorded.last_update == 1787900751
    finally:
        manifest.close()
    messages_json = json.loads(
        (tmp_path / "messages" / "2026-08-27-zolta" / "messages.json").read_bytes()
    )
    assert [m["id"] for m in messages_json] == ["a1", "a2"]


def test_run_messages_tail_fetch_even_when_count_equal(tmp_path: Path) -> None:
    """A changed last_update with no new messages still tail-fetches; the
    duplicate id is deduped and nothing is appended."""
    setup = FakeHttpClient([_conv_page([_conv(CONV_A_ID)]), _msg_page([_msg("a1")]), _msg_page([]), _conv_page([])])
    first = _run_messages(setup, tmp_path)
    assert isinstance(first, Ok)

    changed = _conv_page([_conv(CONV_A_ID, last_update=1787900751)])
    rerun = FakeHttpClient([changed, _msg_page([_msg("a1")]), _msg_page([]), _conv_page([])])
    result = _run_messages(rerun, tmp_path)

    assert isinstance(result, Ok)
    assert (result.value.conversations, result.value.messages) == (1, 0)
    assert rerun.calls[1]["path"].endswith("startingIndex=1")


def test_run_messages_force_refetches_from_zero(tmp_path: Path) -> None:
    setup = FakeHttpClient([_conv_page([_conv(CONV_A_ID)]), _msg_page([_msg("a1")]), _msg_page([]), _conv_page([])])
    first = _run_messages(setup, tmp_path)
    assert isinstance(first, Ok)

    changed = _conv_page([_conv(CONV_A_ID, last_update=1787900751)])
    forced = FakeHttpClient(
        [changed, _msg_page([_msg("a1"), _msg("a2")]), _msg_page([]), _conv_page([])]
    )
    result = _run_messages(forced, tmp_path, force={CONV_A_ID})

    assert isinstance(result, Ok)
    assert forced.calls[1]["path"].endswith("startingIndex=0")
    assert result.value.messages == 2
    messages_json = json.loads(
        (tmp_path / "messages" / "2026-08-27-zolta" / "messages.json").read_bytes()
    )
    assert [m["id"] for m in messages_json] == ["a1", "a2"]


def test_run_messages_truncation_fail_safe(tmp_path: Path) -> None:
    setup = FakeHttpClient(
        [_conv_page([_conv(CONV_A_ID)]), _msg_page([_msg("a1"), _msg("a2")]), _msg_page([]), _conv_page([])]
    )
    first = _run_messages(setup, tmp_path)
    assert isinstance(first, Ok)

    conv_dir = tmp_path / "messages" / "2026-08-27-zolta"
    # simulate truncation: 2 recorded, 1 on disk
    (conv_dir / "messages.json").write_bytes(json.dumps([_msg("a1")]).encode())
    changed = _conv_page([_conv(CONV_A_ID, last_update=1787900751)])
    rerun = FakeHttpClient([changed, _msg_page([_msg("a3")]), _msg_page([]), _conv_page([])])

    result = _run_messages(rerun, tmp_path)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert result.error.subject == "messages_history_truncated"
    messages_json = json.loads((conv_dir / "messages.json").read_bytes())
    assert [m["id"] for m in messages_json] == ["a1"]  # untouched


def test_run_documents_gate_blocks_without_capture(tmp_path: Path) -> None:
    """The drive download URL is unverified (empty drive at spike time);
    the gate refuses before any network call (design US-4 warning)."""
    client = FakeHttpClient([])

    result = asyncio.run(
        run_documents(client=client, session=SESSION, dump_root=tmp_path, log=LOG)  # type: ignore[arg-type]
    )

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "documents_unverified"
    assert client.calls == []


def test_run_messages_account_level_manifest(tmp_path: Path) -> None:
    """The conversations manifest lives at dump/messages/.manifest.sqlite
    (account-level), never under a child slug."""
    client = FakeHttpClient(
        [_conv_page([_conv(CONV_A_ID)]), _msg_page([_msg("a1")]), _msg_page([]), _conv_page([])]
    )
    result = _run_messages(client, tmp_path)
    assert isinstance(result, Ok)
    assert (tmp_path / "messages" / ".manifest.sqlite").is_file()
