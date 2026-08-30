"""Unit tests for the communicator domain models (models/messages.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from inso_dumper.models.messages import (
    Conversation,
    ConversationListPage,
    Message,
    MessageAttachments,
)

SPIKE_DIR = Path("docs/api-notes-data")
CONVERSATIONS_SAMPLE = SPIKE_DIR / "conversations-sample.json"
DETAIL_SAMPLE = SPIKE_DIR / "conversation-detail-sample.json"


def _spike_conversations() -> dict[str, Any]:
    if not CONVERSATIONS_SAMPLE.exists():
        pytest.skip(f"spike corpus absent: {CONVERSATIONS_SAMPLE}")
    return json.loads(CONVERSATIONS_SAMPLE.read_bytes())


def _spike_messages() -> list[dict[str, Any]]:
    if not DETAIL_SAMPLE.exists():
        pytest.skip(f"spike corpus absent: {DETAIL_SAMPLE}")
    return json.loads(DETAIL_SAMPLE.read_bytes())


def _conversation_dict() -> dict[str, Any]:
    return {
        "id": "5b8789d6-4b05-11ed-9234-06f545343a70",
        "lastUpdate": 1787814351,
        "recipient": {
            "name": "Żółta",
            "description": "Wychowawcy w grupie",
            "type": "teachers",
            "prefix": "Grupa:",
        },
        "read": True,
        "excerpt": "Dziękuję",
        "branch": "ee94092c-1022-11ed-94a1-0207c993afca",
        "participantsNames": [],
    }


def _message_dict() -> dict[str, Any]:
    return {
        "id": "4b6763e8-964f-40dc-8c4b-fd8fa833aadf",
        "message": "Dziękuję",
        "sendDate": "czwartek,  9:05",
        "sendTimestamp": 1787814351,
        "sender": {"type": "worker", "name": "Agnieszka", "initials": "BA", "avatar": None},
        "incoming": True,
        "attachments": [],
        "main": False,
        "isRemoved": False,
        "canRemove": False,
    }


# --- Conversation -------------------------------------------------------------


def test_conversation_validates_captured_shape() -> None:
    conv = Conversation.model_validate(_conversation_dict())
    assert conv.id == "5b8789d6-4b05-11ed-9234-06f545343a70"
    assert conv.last_update == 1787814351
    assert conv.recipient.name == "Żółta"
    assert conv.recipient.type == "teachers"
    assert conv.participants_names == []
    assert conv.read is True
    assert conv.excerpt == "Dziękuję"
    assert conv.dumped_at is None  # dumper shadow field, parser leaves it alone


def test_conversation_unknown_key_raises() -> None:
    bad = _conversation_dict() | {"brandNewKey": 1}
    with pytest.raises(ValidationError):
        Conversation.model_validate(bad)


def test_conversation_validates_all_spike_items() -> None:
    payload = _spike_conversations()
    page = ConversationListPage.model_validate(payload)
    assert page.category == "main"
    assert page.unread_count == 0
    assert len(page.conversations) == 2
    assert all(c.id for c in page.conversations)


def test_conversation_list_page_permissive_lists_are_captured_but_ignored() -> None:
    payload = _spike_conversations()
    page = ConversationListPage.model_validate(payload)
    assert page.categories == []
    assert page.templates == []


def test_conversation_list_page_unknown_key_raises() -> None:
    payload = _spike_conversations() | {"brandNewKey": 1}
    with pytest.raises(ValidationError):
        ConversationListPage.model_validate(payload)


# --- Message ------------------------------------------------------------------


def test_message_validates_captured_shape() -> None:
    msg = Message.model_validate(_message_dict())
    assert msg.id == "4b6763e8-964f-40dc-8c4b-fd8fa833aadf"
    assert msg.send_date == "czwartek,  9:05"
    assert msg.send_timestamp == 1787814351
    assert msg.sender.type == "worker"
    assert msg.sender.avatar is None
    assert msg.incoming is True
    assert msg.is_removed is False
    assert msg.can_remove is False


def test_message_unknown_key_raises() -> None:
    bad = _message_dict() | {"brandNewKey": 1}
    with pytest.raises(ValidationError):
        Message.model_validate(bad)


def test_message_attachments_empty_list_normalizes_to_no_attachments() -> None:
    """Captured evidence: 29 of 30 detail messages carry ``attachments:
    []`` (bare empty list), one carries the {media, other} dict. The
    empty list means no attachments and must normalize — a *non-empty*
    list stays a validation error (never observed → drift)."""
    msg = Message.model_validate(_message_dict())
    assert msg.attachments.media == []
    assert msg.attachments.other == []


def test_message_attachments_nonempty_list_is_drift() -> None:
    bad = _message_dict() | {"attachments": [{"surprise": True}]}
    with pytest.raises(ValidationError):
        Message.model_validate(bad)


def test_message_attachments_dict_shape_matches_capture() -> None:
    """The one attachment-bearing captured message: {media: [...],
    other: [...]}; ``other`` is an unverified shape (Open Question 2)
    and stays permissive."""
    d = _message_dict() | {
        "attachments": {
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
    }
    msg = Message.model_validate(d)
    assert len(msg.attachments.media) == 1
    att = msg.attachments.media[0]
    assert att.name == "IMG_8171.jpeg"
    assert att.is_video is False
    assert str(att.url.full).endswith("full.jpg?Expires=1")
    assert msg.attachments.other == []


def test_message_attachments_media_unknown_key_raises() -> None:
    bad = _message_dict() | {
        "attachments": {"media": [{"name": "x", "brandNewKey": 1}], "other": []}
    }
    with pytest.raises(ValidationError):
        Message.model_validate(bad)


def test_message_attachments_other_is_permissive() -> None:
    d = _message_dict() | {"attachments": {"media": [], "other": [{"whoKnows": 1}]}}
    msg = Message.model_validate(d)
    assert msg.attachments.other == [{"whoKnows": 1}]


# --- Alias round-trip ---------------------------------------------------------


def test_message_alias_round_trip() -> None:
    msg = Message.model_validate(_message_dict())
    fresh = Message.model_validate_json(msg.model_dump_json())
    assert fresh == msg


def test_conversation_alias_round_trip() -> None:
    conv = Conversation.model_validate(_conversation_dict())
    fresh = Conversation.model_validate_json(conv.model_dump_json())
    assert fresh == conv


def test_message_attachments_type_exports() -> None:
    from inso_dumper.models.messages import MessageAttachment, MessageSender

    assert MessageAttachments is not None
    assert MessageAttachment is not None
    assert MessageSender is not None
