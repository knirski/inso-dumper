"""Unit tests for the pure dump layout module (dump/layout.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from inso_dumper.dump.layout import dedup_target, derive_post_slug, post_target_dir, sha256_hex
from inso_dumper.models.timeline import Post
from tests.conftest import make_post_dict

DUMP_ROOT = Path("/tmp/dump-root-placeholder")


def _make_post(title: str = "Wycieczka do Leniwej Zagrody") -> Post:
    return Post.model_validate(make_post_dict(title=title))


def test_derive_post_slug_formats_date_and_title() -> None:
    post = _make_post()
    assert derive_post_slug(post) == "2026-08-27-wycieczka-do-leniwej-zagrody"


def test_derive_post_slug_is_idempotent() -> None:
    post = _make_post()
    assert derive_post_slug(post) == derive_post_slug(post)


def test_derive_post_slug_folds_diacritics() -> None:
    post = _make_post(title="Ścieżka w Górach Żółcia")
    slug = derive_post_slug(post)
    assert slug.startswith("2026-08-27-sciezka-w-gorach-zolcia")


def test_post_target_dir_places_post_under_announcements() -> None:
    post = _make_post()
    result = post_target_dir(DUMP_ROOT, "franek-k", post)
    assert result == DUMP_ROOT / "franek-k" / "announcements" / derive_post_slug(post)


def test_dedup_target_photos() -> None:
    digest = "ab" * 32
    expected = DUMP_ROOT / "_common" / "photos" / "ab" / ("ab" * 32 + ".jpg")
    assert dedup_target(DUMP_ROOT, digest, ".jpg", "photos") == expected


def test_dedup_target_videos() -> None:
    digest = "ab" * 32
    expected = DUMP_ROOT / "_common" / "videos" / "ab" / ("ab" * 32 + ".mp4")
    assert dedup_target(DUMP_ROOT, digest, ".mp4", "videos") == expected


def test_dedup_target_attachments() -> None:
    digest = "ab" * 32
    expected = DUMP_ROOT / "_common" / "attachments" / "ab" / ("ab" * 32 + ".pdf")
    assert dedup_target(DUMP_ROOT, digest, ".pdf", "attachments") == expected


def test_sha256_hex_empty_string_digest() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_layout_module_performs_no_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The layout module is pure: Path.exists / Path.mkdir are never called."""

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("layout.py performed IO")

    monkeypatch.setattr(Path, "exists", _fail)
    monkeypatch.setattr(Path, "mkdir", _fail)
    post = _make_post()
    post_target_dir(tmp_path, "franek-k", post)
    dedup_target(tmp_path, "ab" * 32, ".jpg", "photos")
    derive_post_slug(post)
    sha256_hex(b"x")
