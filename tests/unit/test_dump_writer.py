"""Unit tests for the dump writer (dump/writer.py)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump import writer as writer_module
from inso_dumper.dump.layout import sha256_hex
from inso_dumper.dump.writer import link_media_to_post, write_deduped_media, write_post
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.timeline import MediaItemKind
from inso_dumper.models.timeline import Post
from tests.conftest import make_post_dict

LOG = logging.getLogger("test")


def _make_post() -> Post:
    post = Post.model_validate(make_post_dict(content="<p>Witaj <b>świecie</b></p>"))
    return post


def test_write_post_creates_dir_and_three_files(tmp_path: Path) -> None:
    target = tmp_path / "franek-k" / "announcements" / "2026-08-27-wycieczka"
    post = _make_post()

    result = write_post(post, target, log=LOG)

    assert isinstance(result, Ok)
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700
    for name in ("post.json", "post.html", "post.md"):
        f = target / name
        assert f.is_file(), name
        assert f.stat().st_mode & 0o777 == 0o600
    assert (target / "post.html").read_text(encoding="utf-8") == "<p>Witaj <b>świecie</b></p>"
    assert "Witaj" in (target / "post.md").read_text(encoding="utf-8")


def test_write_post_json_round_trips_with_dumped_at(tmp_path: Path) -> None:
    target = tmp_path / "post-dir"
    post = _make_post()

    result = write_post(post, target, log=LOG)

    assert isinstance(result, Ok)
    restored = Post.model_validate_json((target / "post.json").read_bytes())
    assert restored.id == post.id
    assert restored.dumped_at is not None


def test_write_post_oserror_mid_write_removes_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the first file leaves no partial post directory."""
    target = tmp_path / "post-dir"
    post = _make_post()
    calls = {"n": 0}
    real_write = writer_module._write_bytes

    def flaky(path: Path, data: bytes, mode: int) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # post.json succeeded, post.html fails
            raise OSError("disk full")
        real_write(path, data, mode)

    monkeypatch.setattr(writer_module, "_write_bytes", flaky)
    result = write_post(post, target, log=LOG)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert not target.exists()


def test_write_deduped_media_writes_once_then_skips(tmp_path: Path) -> None:
    digest = sha256_hex(b"jpeg")
    first = write_deduped_media(tmp_path, digest, ".jpg", MediaItemKind.PHOTO, b"jpeg", log=LOG)
    assert isinstance(first, Ok)
    assert first.value == tmp_path / "_common" / "photos" / digest[:2] / f"{digest}.jpg"
    assert first.value.read_bytes() == b"jpeg"
    assert first.value.stat().st_mode & 0o777 == 0o600

    second = write_deduped_media(tmp_path, digest, ".jpg", MediaItemKind.PHOTO, b"jpeg", log=LOG)
    assert isinstance(second, Ok)
    assert second.value == first.value
    assert first.value.read_bytes() == b"jpeg"


def test_write_deduped_media_collision_with_different_bytes(tmp_path: Path) -> None:
    digest = "ab" * 32
    target = tmp_path / "_common" / "photos" / "ab" / ("ab" * 32 + ".jpg")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"totally different bytes")

    result = write_deduped_media(tmp_path, digest, ".jpg", MediaItemKind.PHOTO, b"jpeg", log=LOG)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert result.error.subject == "dedup_collision"


def test_link_media_to_post_creates_relative_symlink(tmp_path: Path) -> None:
    post_dir = tmp_path / "child" / "announcements" / "post"
    post_dir.mkdir(parents=True)
    dedup_path = tmp_path / "_common" / "photos" / "ab" / ("ab" * 32 + ".jpg")
    dedup_path.parent.mkdir(parents=True)
    dedup_path.write_bytes(b"jpeg")

    result = link_media_to_post(post_dir, dedup_path, MediaItemKind.PHOTO, 1, log=LOG)

    assert isinstance(result, Ok)
    link = result.value
    assert link == post_dir / "photos" / "1.jpg"
    assert link.is_symlink()
    assert link.resolve() == dedup_path.resolve()


def test_link_media_to_post_attachment_uses_files_dir(tmp_path: Path) -> None:
    post_dir = tmp_path / "post"
    post_dir.mkdir()
    dedup_path = tmp_path / "_common" / "attachments" / "12" / ("12" * 16 + ".pdf")
    dedup_path.parent.mkdir(parents=True)
    dedup_path.write_bytes(b"pdf")

    result = link_media_to_post(post_dir, dedup_path, MediaItemKind.ATTACHMENT, 1, log=LOG)

    assert isinstance(result, Ok)
    assert result.value == post_dir / "files" / "1.pdf"


def test_link_media_to_post_video_uses_videos_dir(tmp_path: Path) -> None:
    post_dir = tmp_path / "post"
    post_dir.mkdir()
    dedup_path = tmp_path / "_common" / "videos" / "cd" / ("cd" * 16 + ".mp4")
    dedup_path.parent.mkdir(parents=True)
    dedup_path.write_bytes(b"mp4")

    result = link_media_to_post(post_dir, dedup_path, MediaItemKind.VIDEO, 1, log=LOG)

    assert isinstance(result, Ok)
    assert result.value == post_dir / "videos" / "1.mp4"


def test_link_media_to_post_falls_back_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_dir = tmp_path / "post"
    post_dir.mkdir()
    dedup_path = tmp_path / "_common" / "photos" / "ab" / ("ab" * 32 + ".jpg")
    dedup_path.parent.mkdir(parents=True)
    dedup_path.write_bytes(b"jpeg")

    def refuse(self: Path, target: str, **kwargs: object) -> None:
        raise OSError("filesystem rejects symlinks")

    monkeypatch.setattr(Path, "symlink_to", refuse)
    result = link_media_to_post(post_dir, dedup_path, MediaItemKind.PHOTO, 1, log=LOG)

    assert isinstance(result, Ok)
    link = result.value
    assert link.is_file() and not link.is_symlink()
    assert link.read_bytes() == b"jpeg"


def test_writer_open_error_returns_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_open(*args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(os, "open", broken_open)
    post = _make_post()
    result = write_post(post, tmp_path / "post-dir", log=LOG)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
