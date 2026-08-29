"""Timeline domain models: Category, Post, Photo, Video, Attachment, Media.

Functional core: no IO, no httpx. These models mirror the captured API
payload (docs/api-notes-data/, summarised in the spec's Findings) with
``extra="forbid"`` so an unknown key raises ``ValidationError`` — the
parser turns that into ``Err(PLATFORM_CHANGED)`` and the dumper fails
loud instead of silently dropping fields.

Keys that are captured but functionally ignored in v1 (``actions``,
``userVoted``, ``likesText``, …) are still modelled: dropping them from
the model would make any *real* unknown key indistinguishable from the
known-but-ignored ones.

The API uses camelCase keys; the model uses snake_case with
``populate_by_name`` so both spellings validate. Serialisation back to
JSON (``post.json`` in the dump) uses the Python field names.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Category(IntEnum):
    """Closed mirror of the platform's data-categories for the in-scope
    ids (2 = Ogłoszenia, 3 = Galerie zdjęć)."""

    ANNOUNCEMENTS = 2
    GALLERIES = 3

    @property
    def api_id(self) -> int:
        return int(self)


def ext_from(name: str, url: str = "") -> str:
    """Derive a file extension (leading dot included).

    Preference order: the original filename's suffix, then the URL
    path's suffix, then ``.bin``. A suffix must be short and
    alphanumeric so query strings or dotfiles don't fool it.
    """
    path = urlsplit(url).path
    for candidate in (name, path.rsplit("/", 1)[-1]):
        _, dot, suffix = candidate.rpartition(".")
        if dot and suffix and suffix.isalnum() and len(suffix) <= 5:
            return f".{suffix.lower()}"
    return ".bin"


class PhotoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thumb: HttpUrl
    full: HttpUrl


class Photo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["photo"]
    name: str
    src: PhotoSource

    @property
    def ext(self) -> str:
        return ext_from(self.name, str(self.src.full))


class Video(BaseModel):
    """Working assumption — no video-bearing post was captured by the
    spike (``videos`` was always ``[]``). The shape mirrors the photo
    item, the closest captured analog. A video-bearing post must be
    captured and this model confirmed before a video-dumping release;
    ``extra="forbid"`` makes a mismatched real shape fail loud."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["video"]
    name: str
    src: PhotoSource

    @property
    def ext(self) -> str:
        return ext_from(self.name, str(self.src.full))


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    url: HttpUrl

    @property
    def ext(self) -> str:
        return ext_from(self.name, str(self.url))


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    photos: list[Photo] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)


class Post(BaseModel):
    """Typed mirror of one ``/system-api/timeline/posts`` item."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    title: str
    content: str  # sanitised HTML
    media: Media
    created_at: datetime = Field(alias="createdAt")  # unix seconds
    created_at_text: str = Field(alias="createdAtText")  # server-formatted, verbatim
    visible_for: list[str] = Field(alias="visibleFor")  # group names; informational
    visible_for_children: list[str] = Field(
        alias="visibleForChildren"
    )  # child UUIDs; informational
    # Captured but functionally ignored in v1 — kept so extra="forbid"
    # distinguishes real drift from known-but-ignored keys.
    actions: dict[str, str]
    sticky: bool
    archived: bool
    user_voted: bool = Field(alias="userVoted")
    author: str | None
    likes: int
    likes_text: str = Field(alias="likesText")
    comments_available: bool = Field(alias="commentsAvailable")
    comments: int
    comments_text: str = Field(alias="commentsText")
    is_worker: bool = Field(alias="isWorker")
    is_worker_only: bool = Field(alias="isWorkerOnly")
    displayed: bool
    poll: object | None  # nullable unknown; discarded in v1
    translations: list[object]
    # Shadow field set by the dumper at write time (post.json only);
    # the parser leaves it alone on the API path.
    dumped_at: datetime | None = None


class MediaItemKind(StrEnum):
    """Closed vocabulary over the three media lists. The values name the
    ``_common/`` subtree each kind dedups into; the per-post directory
    name is derived in ``dump.writer`` (attachments live under
    ``files/`` there, mirroring the platform)."""

    PHOTO = "photos"
    VIDEO = "videos"
    ATTACHMENT = "attachments"


__all__ = [
    "Attachment",
    "Category",
    "Media",
    "MediaItemKind",
    "Photo",
    "PhotoSource",
    "Post",
    "Video",
    "ext_from",
]
