"""Communicator domain models: Conversation, Message, attachments.

Functional core: no IO, no httpx. These models mirror the captured API
payloads (docs/api-notes-data/conversations-sample.json and
conversation-detail-sample.json, summarised in the spec's Findings)
with ``extra="forbid"`` so an unknown key raises ``ValidationError`` —
the parser turns that into ``Err(PLATFORM_CHANGED)`` and the dumper
fails loud instead of silently dropping fields.

Captured-but-functionally-ignored keys (``categories``,
``templates``, ``branch``, ``read``, …) are still modelled: dropping
them would make any *real* unknown key indistinguishable from the
known-but-ignored ones.

The API uses camelCase keys; the models use snake_case with
``populate_by_name`` so both spellings validate. Serialisation back to
JSON (``conversation.json`` in the dump) uses the Python field names.

Capture-proven quirk (updated Aug 2026 against a 128-message real
conversation): ``attachments`` is polymorphic — the ``{media, other}``
dict on newer messages, a bare list (empty *or* non-empty) on legacy
ones; one historical attachment carries ``url: {thumb, mp4}`` (video,
no ``full``); and a legacy "participant removed" system event has an
empty ``message`` with the person's name in ``isRemoved`` (str).
:class:`Message` normalizes both list shapes to
:class:`MessageAttachments`; unknown keys still fail loud.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, HttpUrl, model_validator


def _normalize_attachments(value: object) -> object:
    """Map either captured ``attachments`` shape to the dict form.

    The bare-list shape (empty on newer no-attachment messages, non-
    empty on legacy ones) becomes ``{"media": [...]}``; the dict shape
    passes through unchanged.
    """
    if isinstance(value, list):
        return {"media": value}
    return value


class ConversationRecipient(BaseModel):
    """``recipient`` on a conversation list item (group-teacher threads)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    type: str
    prefix: str


class Conversation(BaseModel):
    """Typed mirror of one ``/system-api/conversations`` list item."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    # Platform lastUpdate (unix seconds); drives the incremental skip.
    last_update: int = Field(alias="lastUpdate")
    recipient: ConversationRecipient
    read: bool
    branch: str
    participants_names: list[str] = Field(alias="participantsNames")
    excerpt: str
    # Shadow field set by the dumper at write time (conversation.json
    # only); the parser leaves it alone on the API path.
    dumped_at: datetime | None = None


class ConversationListPage(BaseModel):
    """Typed mirror of the ``/system-api/conversations`` envelope."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Captured-but-ignored lists: kept so extra="forbid" accepts the
    # envelope while ignoring contents we never act on.
    categories: list[object] = Field(default_factory=list)
    category: str
    conversations: list[Conversation]
    templates: list[object] = Field(default_factory=list)
    unread_count: int = Field(alias="unreadCount")  # recorded, never acted on


class MessageSender(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str  # 'worker' | 'guardian' in the captures
    name: str
    initials: str
    avatar: str | None


class MessageAttachmentUrls(BaseModel):
    """Attachment URLs. Images carry ``full``; videos carry ``mp4``
    (real-traffic evidence: ``{thumb, mp4}`` with no ``full``). At
    least one of the two must be present; ``best`` is the download
    URL."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    thumb: HttpUrl
    full: HttpUrl | None = None
    mp4: HttpUrl | None = None

    @model_validator(mode="after")
    def _require_download_url(self) -> MessageAttachmentUrls:
        if self.full is None and self.mp4 is None:
            raise ValueError("attachment url needs 'full' or 'mp4'")
        return self

    @property
    def best(self) -> str:
        """The download URL: ``full`` when present, else ``mp4``."""
        if self.full is not None:
            return str(self.full)
        assert self.mp4 is not None  # guaranteed by the validator
        return str(self.mp4)


class MessageAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    url: MessageAttachmentUrls
    is_video: bool = Field(alias="isVideo")


class MessageAttachments(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    media: list[MessageAttachment] = Field(default_factory=list)
    # Shape unverified (none in captures) — parsed permissively and
    # dumped verbatim until captured. See design Open Question 2.
    other: list[object] = Field(default_factory=list)


class Message(BaseModel):
    """Typed mirror of one ``?messages=1`` list item (bare-list page)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    message: str
    send_date: str = Field(alias="sendDate")  # server-formatted, verbatim
    send_timestamp: int = Field(alias="sendTimestamp")  # unix seconds
    sender: MessageSender
    incoming: bool
    attachments: Annotated[MessageAttachments, BeforeValidator(_normalize_attachments)]
    main: bool
    # bool on normal messages; on legacy "participant removed" system
    # events the server stores the person's name here (str) with an
    # empty ``message``. Dumped verbatim either way.
    is_removed: bool | str = Field(alias="isRemoved")
    can_remove: bool = Field(alias="canRemove")


__all__ = [
    "Conversation",
    "ConversationListPage",
    "ConversationRecipient",
    "Message",
    "MessageAttachment",
    "MessageAttachmentUrls",
    "MessageAttachments",
    "MessageSender",
]
