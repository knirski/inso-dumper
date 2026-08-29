"""Unit tests for the timeline domain models (models/timeline.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from inso_dumper.models.timeline import Attachment, Category, Media, Photo, Post, Video

SPIKE_DIR = Path("docs/api-notes-data")
SPIKE_FILES = [
    "timeline-posts-page1-category0.json",
    "timeline-posts-page1-category2.json",
    "timeline-posts-page1-category3.json",
]


def _spike_items(fname: str) -> list[dict[str, Any]]:
    path = SPIKE_DIR / fname
    if not path.exists():
        pytest.skip(f"spike corpus absent: {path}")
    data = json.loads(path.read_bytes())
    return data["items"]


def _photo_dict(name: str = "IMG_2679.jpeg") -> dict[str, Any]:
    return {
        "type": "photo",
        "name": name,
        "src": {
            "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
            "full": "https://file.inso.pl/t/1/full.jpg?Expires=1",
        },
    }


def _attachment_dict(name: str = "pozegnanie-z-dzieckiem.pdf") -> dict[str, Any]:
    return {
        "name": name,
        "url": "https://file.inso.pl/t/1/pozegnanie-z-dzieckiem.pdf?Expires=1",
    }


# --- Category ---------------------------------------------------------------


def test_category_api_ids() -> None:
    assert Category.ANNOUNCEMENTS.api_id == 2
    assert Category.GALLERIES.api_id == 3


# --- Photo / Video / Attachment ext derivation ------------------------------


def test_photo_ext_from_name() -> None:
    photo = Photo.model_validate(_photo_dict("IMG_2679.jpeg"))
    assert photo.ext == ".jpeg"


def test_photo_ext_falls_back_to_url_path() -> None:
    photo = Photo.model_validate(_photo_dict(""))
    assert photo.ext == ".jpg"


def test_photo_ext_falls_back_to_bin_when_nothing_else() -> None:
    photo = Photo.model_validate(
        {
            "type": "photo",
            "name": "",
            "src": {
                "thumb": "https://file.inso.pl/t/1/noext?Expires=1",
                "full": "https://file.inso.pl/t/1/noext?Expires=1",
            },
        }
    )
    assert photo.ext == ".bin"


def test_attachment_ext_from_name() -> None:
    att = Attachment.model_validate(_attachment_dict())
    assert att.ext == ".pdf"


def test_video_matches_captured_shape() -> None:
    """Shape verified against a real video-bearing post (Aug 2026):
    src carries mp4 + thumb, and videos ride inside media.photos[]."""
    video = Video.model_validate(
        {
            "type": "video",
            "name": "VID-20260531-WA0008.mp4",
            "src": {
                "mp4": "https://file.inso.pl/t/1/video.mp4?Expires=1",
                "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
            },
        }
    )
    assert video.ext == ".mp4"
    assert video.url == "https://file.inso.pl/t/1/video.mp4?Expires=1"


def test_media_photos_union_discriminates_video() -> None:
    from inso_dumper.models.timeline import Media

    media = Media.model_validate(
        {
            "photos": [
                _photo_dict(),
                {
                    "type": "video",
                    "name": "klip.mp4",
                    "src": {
                        "mp4": "https://file.inso.pl/t/1/video.mp4",
                        "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                    },
                },
            ]
        }
    )
    assert isinstance(media.photos[0], Photo)
    assert isinstance(media.photos[1], Video)


def test_video_unknown_key_raises() -> None:
    """Fail-loud: an unknown video key must raise, not be dropped."""
    with pytest.raises(ValidationError):
        Video.model_validate(
            {
                "type": "video",
                "name": "klip.mp4",
                "duration": 42,
                "src": {
                    "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                    "full": "https://file.inso.pl/t/1/full.mp4",
                },
            }
        )


# --- Post against the spike corpus ------------------------------------------


def test_post_validates_all_spike_items() -> None:
    count = 0
    for fname in SPIKE_FILES:
        for item in _spike_items(fname):
            post = Post.model_validate(item)
            assert post.id
            assert post.title
            count += 1
    assert count == 30


def test_post_parses_created_at_unix_seconds() -> None:
    item = _spike_items(SPIKE_FILES[1])[0]
    post = Post.model_validate(item)
    assert post.created_at.year >= 2026
    assert post.created_at_text == item["createdAtText"]


def test_post_ignores_captured_but_functionally_ignored_keys() -> None:
    """actions/userVoted/likesText/... are parsed but not used; they must
    still be accepted (they are in the captured payload)."""
    item = _spike_items(SPIKE_FILES[1])[0]
    post = Post.model_validate(item)
    assert isinstance(post.actions, dict)
    assert post.user_voted is False


def test_post_unknown_key_raises_validation_error() -> None:
    item = _spike_items(SPIKE_FILES[1])[0]
    item["brandNewKey"] = 1
    with pytest.raises(ValidationError):
        Post.model_validate(item)


def test_post_dumped_at_defaults_to_none() -> None:
    item = _spike_items(SPIKE_FILES[1])[0]
    post = Post.model_validate(item)
    assert post.dumped_at is None


def test_media_defaults() -> None:
    media = Media()
    assert media.photos == []
    assert media.videos == []
    assert media.attachments == []
