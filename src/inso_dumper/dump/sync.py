"""Sync orchestrators: pages → models → media → manifest → disk.

The only module that wires the HTTP shells, the writers, and the
manifests together. All errors arrive as ``Err`` values from the inner
``CliResult`` surfaces and short-circuit the run; nothing here raises.

- ``run`` — one posts category (announcements/galleries) for one child.
- ``run_messages`` — the account-level communicator: manifest at
  ``dump/messages/.manifest.sqlite``, unchanged conversations skipped by
  ``last_update``, changed conversations tail-fetched via
  ``startingIndex``.
- ``run_documents`` — the account-level drive; gated off until the
  download-URL shape is verified against a non-empty capture (design
  US-4): it fails safe instead of guessing.

A re-run is idempotent: manifests (keyed by platform id) plus the
on-disk state decide what to skip, and partial dumps from interrupted
runs are recovered in place instead of orphaning suffixed duplicates.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.dump.layout import derive_post_slug, sha256_hex
from inso_dumper.dump.manifest import (
    Manifest,
    SettlementManifest,
    open_manifest,
    open_settlements_manifest,
)
from inso_dumper.dump.messages import (
    append_messages,
    link_message_attachment,
    resolve_conversation_dir,
    write_conversation,
    write_conversation_payload,
)
from inso_dumper.dump.settlements import invoice_path, write_invoice, write_settlement_month
from inso_dumper.dump.writer import (
    ensure_private_dir,
    link_media_to_post,
    resolve_post_dir,
    write_deduped_media,
    write_post,
)
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import HttpClient
from inso_dumper.http.messages import fetch_messages, list_conversations
from inso_dumper.http.settlements import fetch_invoice, fetch_settlements_history
from inso_dumper.http.timeline import MediaItemKind, download_media, list_posts
from inso_dumper.models.children import Child
from inso_dumper.models.messages import Message
from inso_dumper.models.session import Session
from inso_dumper.models.timeline import Category, ext_from

log = logging.getLogger("inso_dumper.dump.sync")

# The drive download-URL shape is UNVERIFIED (the spike account's drive
# was empty — design Open Question 1). The gated spike task must capture
# a non-empty drive and pin the file shapes before this flips to True.
_DOCUMENTS_VERIFIED = False


@dataclass(frozen=True, slots=True)
class SyncSummary:
    posts_new: int = 0
    posts_skipped: int = 0
    photos: int = 0
    videos: int = 0
    attachments: int = 0
    seconds: float = 0.0
    conversations: int = 0
    messages: int = 0
    message_attachments: int = 0
    documents: int = 0
    settlements: int = 0
    settlements_skipped: int = 0
    settlements_invoices: int = 0


async def run(
    *,
    client: HttpClient,
    session: Session,
    child: Child,
    category: Category,
    dump_root: Path,
    force: set[str] | frozenset[str] | None = None,
    log: logging.Logger = log,
) -> CliResult[SyncSummary]:
    """Sync one category for one child into ``dump_root``.

    ``force`` holds post slugs whose media must be re-downloaded even
    though the manifest knows them. Returns a ``SyncSummary``; every
    inner ``Err`` short-circuits the run and is returned unchanged.
    """
    start = time.monotonic()
    forced = force or set()
    posts_new = posts_skipped = photos = videos = attachments = 0
    # The writer owns the mode invariant; create the chain explicitly so
    # every level is 0o700, then open the manifest inside it.
    announcements_dir = dump_root / child.slug / "announcements"
    for level in (dump_root, dump_root / child.slug, announcements_dir):
        ensure_private_dir(level)
    manifest_result = open_manifest(announcements_dir / ".manifest.sqlite")
    manifest: Manifest | None
    match manifest_result:
        case Err(error):
            return Err(error)
        case Ok(value):
            manifest = value

    try:
        async for item in list_posts(client, session, child.child_id, category):
            match item:
                case Err(error):
                    return Err(error)
                case Ok(post):
                    pass

            post_slug = derive_post_slug(post)
            recorded = manifest.recorded_dir(post.id)
            if recorded is not None and post_slug not in forced:
                if (announcements_dir / recorded).is_dir():
                    posts_skipped += 1
                    log.info("skipping %s", post_slug)
                    continue

            if post_slug in forced:
                # Drop the stale row first so an interrupted forced
                # re-dump cannot be mistaken for a complete one later.
                cleared = manifest.force_clear(post.id)
                match cleared:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass

            resolved = resolve_post_dir(dump_root, child.slug, post, log=log)
            match resolved:
                case Err(error):
                    return Err(error)
                case Ok(target_dir):
                    pass

            written = write_post(post, target_dir, log=log)
            match written:
                case Err(error):
                    return Err(error)
                case Ok(written_dir):
                    pass

            post_counts = dict.fromkeys(MediaItemKind, 0)
            async for media_item in download_media(client, post):
                match media_item:
                    case Err(error):
                        return Err(error)
                    case Ok((kind, name, url, data)):
                        pass
                digest = sha256_hex(data)
                stored = write_deduped_media(
                    dump_root, digest, ext_from(name, url), kind, data, log=log
                )
                match stored:
                    case Err(error):
                        return Err(error)
                    case Ok(stored_path):
                        pass
                linked = link_media_to_post(
                    written_dir, stored_path, kind, post_counts[kind] + 1, log=log
                )
                match linked:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass
                post_counts[kind] += 1

            media_count = sum(post_counts.values())
            upserted = manifest.upsert(
                post, child.slug, post_slug, target_dir.name, category.api_id, media_count
            )
            match upserted:
                case Err(error):
                    return Err(error)
                case Ok(_):
                    pass
            posts_new += 1
            photos += post_counts[MediaItemKind.PHOTO]
            videos += post_counts[MediaItemKind.VIDEO]
            attachments += post_counts[MediaItemKind.ATTACHMENT]
            log.info("dumped %s (%d media)", post_slug, media_count)
    finally:
        if manifest is not None:
            manifest.close()

    return Ok(
        SyncSummary(
            posts_new=posts_new,
            posts_skipped=posts_skipped,
            photos=photos,
            videos=videos,
            attachments=attachments,
            seconds=time.monotonic() - start,
        )
    )


async def _fetch_attachment(client: HttpClient, url: str) -> CliResult[bytes]:
    """One signed-attachment GET, same transport pattern as
    ``download_media``: the full URL as the request path with a ``Host``
    header, status in the error subject for diagnostics."""
    headers = {"Host": urlsplit(url).netloc}
    result = await client.request("GET", url, headers=headers)
    response = None
    match result:
        case Err(error):
            return Err(error)
        case Ok(value):
            response = value
    if response.status != 200:
        return Err(CliError(kind=CliErrorKind.HTTP, subject=f"media_status_{response.status}"))
    return Ok(response.body)


async def _link_new_attachments(
    client: HttpClient,
    dump_root: Path,
    target_dir: Path,
    new_messages: list[Message],
    *,
    log: logging.Logger = log,
) -> CliResult[int]:
    """Download and link every media attachment of ``new_messages``;
    returns the number linked."""
    linked = 0
    for message in new_messages:
        for n, attachment in enumerate(message.attachments.media, start=1):
            fetched = await _fetch_attachment(client, attachment.url.best)
            match fetched:
                case Err(error):
                    return Err(error)
                case Ok(data):
                    pass
            stored = link_message_attachment(
                dump_root=dump_root,
                conversation_dir=target_dir,
                message_id=message.id,
                n=n,
                name=attachment.name,
                url=attachment.url.best,
                data=data,
                is_video=attachment.is_video,
                log=log,
            )
            match stored:
                case Err(error):
                    return Err(error)
                case Ok(_):
                    pass
            linked += 1
    return Ok(linked)


async def run_messages(
    *,
    client: HttpClient,
    session: Session,
    dump_root: Path,
    force: set[str] | frozenset[str] | None = None,
    log: logging.Logger = log,
) -> CliResult[SyncSummary]:
    """Sync the account's conversations into ``dump_root/'messages'``.

    ``force`` holds conversation ids **or** recorded dir names whose
    history must be re-fetched from 0 (also the truncation recovery).
    Account-level: the manifest lives at
    ``dump_root/'messages'/'.manifest.sqlite'`` and the child argument
    plays no role here.
    """
    start = time.monotonic()
    forced = force or frozenset()
    messages_dir = dump_root / "messages"
    ensure_private_dir(messages_dir, parents=True)
    manifest_result = open_manifest(messages_dir / ".manifest.sqlite")
    manifest: Manifest | None
    match manifest_result:
        case Err(error):
            return Err(error)
        case Ok(value):
            manifest = value

    conversations = messages_added = attachments_linked = 0
    try:
        async for item in list_conversations(client, session):
            match item:
                case Err(error):
                    return Err(error)
                case Ok(conversation):
                    pass

            recorded = manifest.recorded_conversation(conversation.id)
            is_forced = conversation.id in forced or (
                recorded is not None and recorded.dir_name in forced
            )
            if (
                recorded is not None
                and recorded.last_update == conversation.last_update
                and not is_forced
            ):
                log.info("skipping conversation %s (unchanged)", conversation.id)
                continue
            # dir_record keeps the once-assigned directory binding across a
            # forced re-fetch (the date prefix must not chase later activity);
            # tail_record drives only the startingIndex decision.
            dir_record = recorded
            tail_record = recorded
            if is_forced and recorded is not None:
                cleared = manifest.force_clear_conversation(conversation.id)
                match cleared:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass
                tail_record = None

            starting = tail_record.message_count if tail_record is not None else 0
            new_messages: list[Message] = []
            seen_ids: set[str] = set()
            async for page_item in fetch_messages(client, session, conversation.id, starting):
                match page_item:
                    case Err(error):
                        return Err(error)
                    case Ok(page):
                        pass
                for message in page:
                    if message.id not in seen_ids:
                        seen_ids.add(message.id)
                        new_messages.append(message)

            target_dir = resolve_conversation_dir(dump_root, conversation, dir_record)
            if tail_record is not None:
                # Tail path: extend messages.json (fail-safe first), then
                # refresh the conversation payload.
                appended = append_messages(
                    target_dir, new_messages, expected_existing=tail_record.message_count
                )
                match appended:
                    case Err(error):
                        return Err(error)
                    case Ok(added):
                        pass
                payload = write_conversation_payload(conversation, target_dir)
                match payload:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass
                total = tail_record.message_count + appended.value
                messages_added += appended.value
            else:
                written = write_conversation(conversation, new_messages, target_dir)
                match written:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass
                total = len(new_messages)
                messages_added += len(new_messages)

            linked = await _link_new_attachments(
                client, dump_root, target_dir, new_messages, log=log
            )
            match linked:
                case Err(error):
                    return Err(error)
                case Ok(count):
                    pass
            attachments_linked += linked.value

            upserted = manifest.upsert_conversation(
                conversation.id, target_dir.name, conversation.last_update, total
            )
            match upserted:
                case Err(error):
                    return Err(error)
                case Ok(_):
                    pass
            conversations += 1
            log.info("dumped conversation %s (%d new messages)", target_dir.name, len(new_messages))
    finally:
        if manifest is not None:
            manifest.close()

    return Ok(
        SyncSummary(
            conversations=conversations,
            messages=messages_added,
            message_attachments=attachments_linked,
            seconds=time.monotonic() - start,
        )
    )


async def run_settlements(
    *,
    client: HttpClient,
    config: Config,
    session: Session,
    child: Child,
    dump_root: Path,
    force: set[str] | frozenset[str] | None = None,
    log: logging.Logger = log,
) -> CliResult[SyncSummary]:
    """Sync one child's settlement history into ``dump_root``.

    Per-child (the history URL is per child, unlike the account-level
    messages/documents walks): manifest at
    ``dump_root/<child-slug>/settlements/.manifest.sqlite`` (schema
    v4). Every run re-scrapes the history page (one GET) and rewrites
    each ``settlement.json`` idempotently; an invoice is downloaded
    only when the manifest row lacks ``invoice_downloaded_at`` or the
    file is absent (or the month is forced — the row is cleared first).
    ``force`` holds ``YYYY-MM`` keys. Every inner ``Err`` short-circuits
    and is returned unchanged; nothing here raises.
    """
    start = time.monotonic()
    forced = force or frozenset()
    settlements_root = dump_root / child.slug / "settlements"
    # Create and enforce each level so the whole chain is 0o700.
    for level in (dump_root, dump_root / child.slug, settlements_root):
        ensure_private_dir(level)
    manifest_result = open_settlements_manifest(settlements_root / ".manifest.sqlite")
    manifest: SettlementManifest | None
    match manifest_result:
        case Err(error):
            return Err(error)
        case Ok(value):
            manifest = value

    months = invoices = skipped = 0
    try:
        async for item in fetch_settlements_history(client, config, session, child.child_id):
            match item:
                case Err(error):
                    return Err(error)
                case Ok(page):
                    pass

            for month in page.months:
                month_dir = settlements_root / month.month
                written = write_settlement_month(month, month_dir)
                match written:
                    case Err(error):
                        return Err(error)
                    case Ok(_):
                        pass

                recorded = manifest.recorded_settlement(month.month)
                if month.month in forced:
                    # Drop the stale row first so an interrupted forced
                    # re-download cannot be mistaken for a complete one.
                    cleared = manifest.force_clear_settlements(month.month)
                    match cleared:
                        case Err(error):
                            return Err(error)
                        case Ok(_):
                            pass
                    recorded = None

                if month.invoice_bill_uuid is None:
                    if recorded is None:
                        # Record first-seen for invoice-less months; a
                        # row that already carries download history is
                        # never erased by a link-less card.
                        upserted = manifest.upsert_settlement(month.month, None, None)
                        match upserted:
                            case Err(error):
                                return Err(error)
                            case Ok(_):
                                pass
                    months += 1
                    continue

                need_invoice = recorded is None or (
                    recorded.invoice_downloaded_at is None or not invoice_path(month_dir).exists()
                )
                if need_invoice:
                    fetched = await fetch_invoice(client, config, session, month.invoice_bill_uuid)
                    match fetched:
                        case Err(error):
                            return Err(error)
                        case Ok(data):
                            pass
                    saved = write_invoice(data, month_dir)
                    match saved:
                        case Err(error):
                            return Err(error)
                        case Ok(_):
                            pass
                    upserted = manifest.upsert_settlement(
                        month.month, month.invoice_bill_uuid, datetime.now(UTC).isoformat()
                    )
                    match upserted:
                        case Err(error):
                            return Err(error)
                        case Ok(_):
                            pass
                    invoices += 1
                else:
                    # Refresh the bill uuid but keep the download stamp.
                    assert recorded is not None  # need_invoice False implies this
                    upserted = manifest.upsert_settlement(
                        month.month,
                        month.invoice_bill_uuid,
                        recorded.invoice_downloaded_at,
                    )
                    match upserted:
                        case Err(error):
                            return Err(error)
                        case Ok(_):
                            pass
                    skipped += 1
                months += 1
    finally:
        if manifest is not None:
            manifest.close()

    return Ok(
        SyncSummary(
            settlements=months,
            settlements_skipped=skipped,
            settlements_invoices=invoices,
            seconds=time.monotonic() - start,
        )
    )


async def run_documents(
    *,
    client: HttpClient,
    session: Session,
    dump_root: Path,
    log: logging.Logger = log,
) -> CliResult[SyncSummary]:
    """Sync the account's drive into ``dump_root/'documents'``.

    Gated (design US-4 / Open Question 1): only ``breadcrumbs`` of the
    drive API is verified — the file/download shapes are provisional.
    Rather than guess, the category fails safe; CONFIG (exit 2) because
    the gate is missing verified platform knowledge, not a transport
    failure. Flip ``_DOCUMENTS_VERIFIED`` only after the gated spike
    task pins the shapes from a non-empty drive capture.
    """
    if not _DOCUMENTS_VERIFIED:
        log.warning("documents category skipped: drive download URL not verified")
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="documents_unverified"))
    raise AssertionError("unreachable until the gated spike task implements the walk")


__all__ = ["SyncSummary", "run", "run_documents", "run_messages", "run_settlements"]
