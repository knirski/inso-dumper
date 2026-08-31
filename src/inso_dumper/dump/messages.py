"""Conversation disk writer: naming, atomic writes, attachment links.

The disk boundary for the messages category (the design's layer table):
owns everything on-disk about ``dump/messages/`` — the once-assigned
directory name, the two JSON files, and the attachment links into the
shared ``_common/`` store. All ``OSError`` become
``Err(CliError(INTERNAL, subject='messages_<op>'))``; nothing here
raises past the boundary.

Layout per conversation:

    dump/messages/<YYYY-MM-DD>-<recipient-slug>/
    ├── conversation.json      # list payload (dumped_at stamped)
    ├── messages.json          # full history, merged tail-wise
    └── attachments/<message-id>/<n><ext> -> ../../../../_common/<kind>/…

Truncation fail-safe (design.md Data Model): ``append_messages`` with
``expected_existing`` set refuses to append a tail onto a file whose
message count disagrees with the manifest — the recovery is a
``--force`` full re-fetch, never a silently-widened hole.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.dump.layout import sha256_hex
from inso_dumper.dump.manifest import ConversationRecord
from inso_dumper.dump.writer import _write_bytes, ensure_private_dir, write_deduped_media
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.messages import Conversation, Message
from inso_dumper.models.timeline import MediaItemKind, ext_from

log = logging.getLogger("inso_dumper.dump.messages")

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".avif"})

_CONVERSATION_JSON = "conversation.json"
_MESSAGES_JSON = "messages.json"


def _messages_root(dump_root: Path) -> Path:
    return dump_root / "messages"


def _dir_name_for(conversation: Conversation) -> str:
    date = datetime.fromtimestamp(conversation.last_update, UTC).date().isoformat()
    from inso_dumper.models.children import _SLUG_RE, _ascii_fold

    slug = _SLUG_RE.sub("-", _ascii_fold(conversation.recipient.name).lower()).strip("-")
    return f"{date}-{slug or 'conversation'}"


def _conversation_id_on_disk(directory: Path) -> str | None:
    """The conversation id recorded in the directory's
    ``conversation.json``, or ``None`` when absent/unreadable."""
    try:
        payload = json.loads((directory / _CONVERSATION_JSON).read_bytes())
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    conversation_id = payload.get("id")
    return conversation_id if isinstance(conversation_id, str) else None


def resolve_conversation_dir(
    dump_root: Path, conversation: Conversation, recorded: ConversationRecord | None
) -> Path:
    """Pick the destination directory for ``conversation``.

    - A recorded directory name wins — it was assigned once (design:
      the date prefix does not chase later activity).
    - A fresh name is ``<lastUpdate date>-<slugified recipient>``; a
      collision with a *foreign* conversation's directory gets the next
      ``-N`` suffix (the posts recovery rule). A directory whose
      ``conversation.json`` names this conversation is a partial dump
      from an interrupted run and is reused in place.
    """
    if recorded is not None:
        return _messages_root(dump_root) / recorded.dir_name
    base = _messages_root(dump_root) / _dir_name_for(conversation)
    if not base.exists() or _conversation_id_on_disk(base) == conversation.id:
        return base
    n = 2
    while True:
        candidate = base.parent / f"{base.name}-{n}"
        if not candidate.exists() or _conversation_id_on_disk(candidate) == conversation.id:
            return candidate
        n += 1


def write_conversation_payload(conversation: Conversation, target_dir: Path) -> CliResult[None]:
    """(Re)write only ``conversation.json`` — the tail-append path uses
    this to refresh the list payload (``lastUpdate``, ``excerpt``,
    ``read``) without touching ``messages.json``."""
    stamped = conversation.model_copy(update={"dumped_at": datetime.now(UTC)})
    try:
        ensure_private_dir(target_dir)
        _write_bytes(target_dir / _CONVERSATION_JSON, stamped.model_dump_json().encode("utf-8"))
    except OSError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_conversation_write"))
    return Ok(None)


def write_conversation(
    conversation: Conversation, messages: list[Message], target_dir: Path
) -> CliResult[Path]:
    """Write ``conversation.json`` + ``messages.json`` atomically (0600
    files, 0700 dirs). Any ``OSError`` is
    ``Err(INTERNAL, 'messages_conversation_write')``."""
    stamped = conversation.model_copy(update={"dumped_at": datetime.now(UTC)})
    messages_bytes = json.dumps(
        [m.model_dump(mode="json") for m in messages], ensure_ascii=False
    ).encode("utf-8")
    try:
        ensure_private_dir(target_dir, parents=True)
        payload_result = write_conversation_payload(stamped, target_dir)
        if isinstance(payload_result, Err):
            return payload_result
        _write_bytes(target_dir / _MESSAGES_JSON, messages_bytes)
    except OSError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_conversation_write"))
    log.info("wrote %s (%d messages)", target_dir, len(messages))
    return Ok(target_dir)


def append_messages(
    conversation_dir: Path, new_messages: list[Message], *, expected_existing: int | None = None
) -> CliResult[int]:
    """Merge ``new_messages`` into ``messages.json`` by message id.

    Existing order is preserved, already-present ids are skipped, and
    the rewritten file is atomic. Returns the number added. When
    ``expected_existing`` is given, an on-disk count that disagrees is
    ``Err(INTERNAL, 'messages_history_truncated')`` — the tail is never
    appended onto a file that disagrees with the manifest.
    """
    history_path = conversation_dir / _MESSAGES_JSON
    try:
        existing: list[dict[str, object]] = (
            json.loads(history_path.read_bytes()) if history_path.exists() else []
        )
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_history_read"))
    if expected_existing is not None and len(existing) != expected_existing:
        log.warning(
            "messages.json holds %d messages, manifest says %d: refusing to append",
            len(existing),
            expected_existing,
        )
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_history_truncated"))
    seen_ids = {m.get("id") for m in existing if isinstance(m, dict)}
    added = 0
    for message in new_messages:
        if message.id in seen_ids:
            continue
        existing.append(message.model_dump(mode="json"))
        seen_ids.add(message.id)
        added += 1
    if added:
        try:
            _write_bytes(
                history_path,
                json.dumps(existing, ensure_ascii=False).encode("utf-8"),
            )
        except OSError:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_append"))
    log.info("appended %d messages to %s", added, history_path)
    return Ok(added)


def link_message_attachment(
    *,
    dump_root: Path,
    conversation_dir: Path,
    message_id: str,
    n: int,
    name: str,
    url: str,
    data: bytes,
    is_video: bool,
    log: logging.Logger = log,
) -> CliResult[Path]:
    """Store one attachment's bytes in ``_common/`` and link it into
    ``conversation_dir/attachments/<message-id>/<n><ext>``.

    Kind mapping (design US-2): ``is_video`` → ``videos/``; an image
    extension → ``photos/``; anything else → ``attachments/``. The link
    is relative; a filesystem that rejects symlinks gets a copy.
    """
    ext = ext_from(name, url)
    if is_video:
        kind = MediaItemKind.VIDEO
    elif ext in _IMAGE_EXTS:
        kind = MediaItemKind.PHOTO
    else:
        kind = MediaItemKind.ATTACHMENT
    digest = sha256_hex(data)
    stored = write_deduped_media(dump_root, digest, ext, kind, data, log=log)
    match stored:
        case Err(error):
            return Err(error)
        case Ok(dedup_path):
            pass
    link = conversation_dir / "attachments" / message_id / f"{n}{ext}"
    rel_target = os.path.relpath(dedup_path, link.parent)
    try:
        ensure_private_dir(link.parent, parents=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        try:
            link.symlink_to(rel_target)
        except OSError:
            log.info("symlink unsupported for %s; copying", link)
            shutil.copy2(dedup_path, link)
    except OSError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="messages_attachment_link"))
    log.info("linked %s -> %s", link, rel_target)
    return Ok(link)


__all__ = [
    "append_messages",
    "link_message_attachment",
    "resolve_conversation_dir",
    "write_conversation",
    "write_conversation_payload",
]
