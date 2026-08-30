"""Unit tests for the conversation disk writer (dump/messages.py)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump.manifest import ConversationRecord
from inso_dumper.dump.messages import (
    append_messages,
    link_message_attachment,
    resolve_conversation_dir,
    write_conversation,
)
from inso_dumper.errors import CliErrorKind
from inso_dumper.models.messages import Conversation, Message

LOG = logging.getLogger("test")


def _conversation(id: str = "5b8789d6-4b05-11ed-9234-06f545343a70") -> Conversation:
    return Conversation.model_validate(
        {
            "id": id,
            "lastUpdate": 1787814351,  # 2026-08-27 UTC
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
    )


def _message(id: str) -> Message:
    return Message.model_validate(
        {
            "id": id,
            "message": "hello",
            "sendDate": "czwartek,  9:05",
            "sendTimestamp": 1787814351,
            "sender": {"type": "worker", "name": "A", "initials": "A", "avatar": None},
            "incoming": True,
            "attachments": [],
            "main": False,
            "isRemoved": False,
            "canRemove": False,
        }
    )


# --- resolve_conversation_dir ---------------------------------------------------


def test_dir_name_formats_date_and_slugged_recipient(tmp_path: Path) -> None:
    conv = _conversation()
    target = resolve_conversation_dir(tmp_path, conv, None)
    assert target == tmp_path / "messages" / "2026-08-27-zolta"


def test_recorded_dir_name_wins(tmp_path: Path) -> None:
    conv = _conversation()
    recorded = ConversationRecord(
        conversation_id=conv.id,
        dir_name="2026-08-27-zolta-3",
        last_update=conv.last_update,
        message_count=30,
    )
    target = resolve_conversation_dir(tmp_path, conv, recorded)
    assert target == tmp_path / "messages" / "2026-08-27-zolta-3"


def test_foreign_collision_gets_suffix(tmp_path: Path) -> None:
    conv = _conversation()
    taken = tmp_path / "messages" / "2026-08-27-zolta"
    taken.mkdir(parents=True)
    (taken / "conversation.json").write_text(
        json.dumps({"id": "another-conversation"}), encoding="utf-8"
    )
    target = resolve_conversation_dir(tmp_path, conv, None)
    assert target == tmp_path / "messages" / "2026-08-27-zolta-2"


def test_same_conversation_collision_is_reused(tmp_path: Path) -> None:
    """A dir whose conversation.json names the same conversation is the
    partial dump of an interrupted run — reuse it, don't suffix."""
    conv = _conversation()
    taken = tmp_path / "messages" / "2026-08-27-zolta"
    taken.mkdir(parents=True)
    (taken / "conversation.json").write_text(json.dumps({"id": conv.id}), encoding="utf-8")
    target = resolve_conversation_dir(tmp_path, conv, None)
    assert target == taken


def test_unreadable_collision_json_gets_suffix(tmp_path: Path) -> None:
    conv = _conversation()
    taken = tmp_path / "messages" / "2026-08-27-zolta"
    taken.mkdir(parents=True)
    (taken / "conversation.json").write_bytes(b"\xff\xfe not json")
    target = resolve_conversation_dir(tmp_path, conv, None)
    assert target == tmp_path / "messages" / "2026-08-27-zolta-2"


# --- write_conversation ----------------------------------------------------------


def test_write_conversation_creates_both_jsons(tmp_path: Path) -> None:
    conv = _conversation()
    messages = [_message("m1"), _message("m2")]
    target_dir = tmp_path / "messages" / "2026-08-27-zolta"

    result = write_conversation(conv, messages, target_dir)

    assert isinstance(result, Ok)
    assert result.value == target_dir
    for path in (target_dir / "conversation.json", target_dir / "messages.json"):
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o600
    conversation_payload = json.loads((target_dir / "conversation.json").read_bytes())
    assert conversation_payload["id"] == conv.id
    assert conversation_payload["dumped_at"] is not None
    history = json.loads((target_dir / "messages.json").read_bytes())
    assert [m["id"] for m in history] == ["m1", "m2"]


def test_write_conversation_oserror_is_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_write_bytes(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    import inso_dumper.dump.messages as mod

    monkeypatch.setattr(mod, "_write_bytes", broken_write_bytes)
    result = write_conversation(_conversation(), [_message("m1")], tmp_path / "messages" / "d")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert result.error.subject == "messages_conversation_write"


# --- append_messages -------------------------------------------------------------


def test_append_messages_merges_by_id_and_returns_added(tmp_path: Path) -> None:
    target_dir = tmp_path / "messages" / "2026-08-27-zolta"
    write_conversation(_conversation(), [_message("m1"), _message("m2")], target_dir)

    result = append_messages(target_dir, [_message("m2"), _message("m3"), _message("m4")])

    assert isinstance(result, Ok)
    assert result.value == 2  # m2 already present
    history = json.loads((target_dir / "messages.json").read_bytes())
    assert [m["id"] for m in history] == ["m1", "m2", "m3", "m4"]  # order preserved


def test_append_messages_idempotent_on_retry(tmp_path: Path) -> None:
    target_dir = tmp_path / "messages" / "2026-08-27-zolta"
    write_conversation(_conversation(), [_message("m1")], target_dir)
    new = [_message("m2")]

    first = append_messages(target_dir, new)
    again = append_messages(target_dir, new)

    assert isinstance(first, Ok) and first.value == 1
    assert isinstance(again, Ok) and again.value == 0
    history = json.loads((target_dir / "messages.json").read_bytes())
    assert [m["id"] for m in history] == ["m1", "m2"]


def test_append_messages_truncation_fail_safe(tmp_path: Path) -> None:
    """A messages.json whose count disagrees with the manifest must not
    receive a tail (the loss would become permanent)."""
    target_dir = tmp_path / "messages" / "2026-08-27-zolta"
    write_conversation(_conversation(), [_message("m1"), _message("m2")], target_dir)
    (target_dir / "messages.json").write_bytes(json.dumps([{"id": "m1"}]).encode())

    result = append_messages(target_dir, [_message("m3")], expected_existing=2)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert result.error.subject == "messages_history_truncated"
    history = json.loads((target_dir / "messages.json").read_bytes())
    assert [m["id"] for m in history] == ["m1"]  # no tail appended


def test_append_messages_oserror_is_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "messages" / "2026-08-27-zolta"
    write_conversation(_conversation(), [_message("m1")], target_dir)

    def broken_replace(*args: object, **kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr("inso_dumper.dump.messages.os.replace", broken_replace)
    result = append_messages(target_dir, [_message("m2")])
    assert isinstance(result, Err)
    assert result.error.subject == "messages_append"


# --- link_message_attachment -------------------------------------------------------


def _attachment_data() -> tuple[dict[str, Any], bytes]:
    return {
        "name": "IMG_8171.jpeg",
        "url": {
            "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
            "full": "https://file.inso.pl/t/1/full.jpg?Expires=1",
        },
        "isVideo": False,
    }, b"image-bytes"


def test_link_message_attachment_image_goes_to_photos(tmp_path: Path) -> None:
    conv_dir = tmp_path / "messages" / "d"
    conv_dir.mkdir(parents=True)
    payload, data = _attachment_data()

    result = link_message_attachment(
        dump_root=tmp_path,
        conversation_dir=conv_dir,
        message_id="m1",
        n=1,
        name=payload["name"],
        url=str(payload["url"]["full"]),
        data=data,
        is_video=False,
        log=LOG,
    )

    assert isinstance(result, Ok)
    assert result.value == conv_dir / "attachments" / "m1" / "1.jpeg"
    blob = tmp_path / "_common" / "photos"
    assert any(p.suffix == ".jpeg" for p in blob.rglob("*.jpeg"))
    assert result.value.resolve() == next(blob.rglob("*.jpeg")).resolve()


def test_link_message_attachment_video_goes_to_videos(tmp_path: Path) -> None:
    conv_dir = tmp_path / "messages" / "d"
    conv_dir.mkdir(parents=True)

    result = link_message_attachment(
        dump_root=tmp_path,
        conversation_dir=conv_dir,
        message_id="m2",
        n=1,
        name="clip.mp4",
        url="https://file.inso.pl/t/1/clip.mp4?Expires=1",
        data=b"video-bytes",
        is_video=True,
        log=LOG,
    )

    assert isinstance(result, Ok)
    assert result.value.name == "1.mp4"
    assert result.value.parent.parent.name == "attachments"  # link layout is fixed
    assert result.value.resolve().parent.parent.name == "videos"  # blob kind


def test_link_message_attachment_non_image_goes_to_attachments(tmp_path: Path) -> None:
    conv_dir = tmp_path / "messages" / "d"
    conv_dir.mkdir(parents=True)

    result = link_message_attachment(
        dump_root=tmp_path,
        conversation_dir=conv_dir,
        message_id="m3",
        n=1,
        name="raport.pdf",
        url="https://file.inso.pl/t/1/raport.pdf?Expires=1",
        data=b"pdf-bytes",
        is_video=False,
        log=LOG,
    )

    assert isinstance(result, Ok)
    assert result.value.name == "1.pdf"
    assert result.value.parent.parent.name == "attachments"
    assert result.value.resolve().parent.parent.name == "attachments"


def test_link_message_attachment_oserror_is_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv_dir = tmp_path / "messages" / "d"
    conv_dir.mkdir(parents=True)

    def broken_symlink(*args: object, **kwargs: object) -> None:
        raise OSError("no links")

    def broken_copy2(*args: object, **kwargs: object) -> None:
        raise OSError("no copies either")

    monkeypatch.setattr(Path, "symlink_to", broken_symlink)
    monkeypatch.setattr("inso_dumper.dump.messages.shutil.copy2", broken_copy2)

    result = link_message_attachment(
        dump_root=tmp_path,
        conversation_dir=conv_dir,
        message_id="m1",
        n=1,
        name="IMG_1.jpeg",
        url="https://file.inso.pl/t/1/full.jpg?Expires=1",
        data=b"x",
        is_video=False,
        log=LOG,
    )
    assert isinstance(result, Err)
    assert result.error.subject == "messages_attachment_link"
