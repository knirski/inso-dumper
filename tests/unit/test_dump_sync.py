"""Unit tests for the dump sync orchestrator (dump/sync.py)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump import sync as sync_module
from inso_dumper.dump.layout import derive_post_slug
from inso_dumper.dump.sync import run
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


def _run(client: FakeHttpClient, tmp_path: Path, **kwargs: Any):
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
        videos=[
            {
                "type": "video",
                "name": "klip.mp4",
                "src": {
                    "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                    "full": "https://file.inso.pl/t/1/full.mp4",
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


def test_manifest_entry_without_directory_is_redumped(tmp_path: Path) -> None:
    """Design: skip requires BOTH the manifest entry and the on-disk dir."""
    posts = [_make_post()]
    client = FakeHttpClient([_page(_post_dicts(posts)), _page([])])
    first = _run(client, tmp_path)
    assert isinstance(first, Ok)

    post_dir = tmp_path / "franek-k" / "announcements" / derive_post_slug(posts[0])
    import shutil

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
