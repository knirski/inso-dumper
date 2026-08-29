"""Sync orchestrator: pages → posts → media → manifest.

The only module that wires the timeline shells, the writer, and the
manifest together. All errors arrive as ``Err`` values from the inner
``CliResult`` surfaces and short-circuit the run; nothing here raises.
A re-run is idempotent: the manifest (keyed by ``post_id``) plus the
on-disk directory decide what to skip, and a partial dump from an
interrupted run is recovered in place instead of orphaning a suffixed
duplicate directory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.dump.layout import derive_post_slug, sha256_hex
from inso_dumper.dump.manifest import Manifest, open_manifest
from inso_dumper.dump.writer import (
    ensure_private_dir,
    link_media_to_post,
    resolve_post_dir,
    write_deduped_media,
    write_post,
)
from inso_dumper.errors import CliResult
from inso_dumper.http.client import HttpClient
from inso_dumper.http.timeline import MediaItemKind, download_media, list_posts
from inso_dumper.models.children import Child
from inso_dumper.models.session import Session
from inso_dumper.models.timeline import Category, Post, ext_from

log = logging.getLogger("inso_dumper.dump.sync")


@dataclass(frozen=True, slots=True)
class SyncSummary:
    posts_new: int
    posts_skipped: int
    photos: int
    videos: int
    attachments: int
    seconds: float


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
                    case Ok((kind, name, data)):
                        pass
                digest = sha256_hex(data)
                stored = write_deduped_media(dump_root, digest, ext_from(name), kind, data, log=log)
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


__all__ = ["SyncSummary", "run"]
