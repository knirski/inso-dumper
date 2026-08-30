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

Capture-proven quirk: ``attachments`` is polymorphic — a bare empty
list when a message has none, the ``{media, other}`` dict when it
does. :class:`Message` normalizes ``[]`` to an empty
:class:`MessageAttachments`; a *non-empty* list is drift and fails
validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, HttpUrl


def _empty_list_is_no_attachments(value: object) -> object:
    """Map the captured ``attachments: []`` to the empty dict shape."""
    if isinstance(value, list) and not value:
        return {}
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
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    thumb: HttpUrl
    full: HttpUrl


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
    attachments: Annotated[
        MessageAttachments, BeforeValidator(_empty_list_is_no_attachments)
    ]
    main: bool
    is_removed: bool = Field(alias="isRemoved")
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
