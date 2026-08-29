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

import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.dump.layout import derive_post_slug, post_target_dir, sha256_hex
from inso_dumper.dump.manifest import Manifest, open_manifest
from inso_dumper.dump.writer import link_media_to_post, write_deduped_media, write_post
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


def _is_same_post_on_disk(target_dir: Path, post: Post) -> bool:
    """True if ``target_dir`` holds a ``post.json`` naming the same post.

    That means a partial dump from an interrupted run (the manifest was
    never upserted) — recoverable by re-dumping in place.
    """
    try:
        data = json.loads((target_dir / "post.json").read_bytes())
        return data.get("id") == post.id
    except OSError, json.JSONDecodeError:
        return False


def _resolve_target_dir(dump_root: Path, child_slug: str, post: Post) -> Path:
    """Pick the destination for ``post``.

    - fresh path → use it;
    - existing dir whose ``post.json`` names the same ``post_id`` →
      reuse it (partial-run recovery);
    - existing dir with a different/unreadable ``post.json`` → slug
      collision; append the next ``-N`` suffix.
    """
    base = post_target_dir(dump_root, child_slug, post)
    if not base.exists() or _is_same_post_on_disk(base, post):
        return base
    n = 1
    while True:
        candidate = base.parent / f"{base.name}-{n}"
        if not candidate.exists() or _is_same_post_on_disk(candidate, post):
            return candidate
        n += 1


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
    manifest_result = open_manifest(dump_root / child.slug / "announcements" / ".manifest.sqlite")
    manifest: Manifest | None
    match manifest_result:
        case Err(error):
            return Err(error)
        case Ok(value):
            manifest = value

    posts_new = posts_skipped = photos = videos = attachments = 0
    try:
        async for item in list_posts(client, session, child.child_id, category):
            match item:
                case Err(error):
                    return Err(error)
                case Ok(post):
                    pass

            post_slug = derive_post_slug(post)
            skip_dir = dump_root / child.slug / "announcements" / post_slug
            if manifest.already_dumped(post.id) and post_slug not in forced and skip_dir.is_dir():
                posts_skipped += 1
                log.info("skipping %s", post_slug)
                continue

            target_dir = _resolve_target_dir(dump_root, child.slug, post)
            if target_dir.exists():
                # The only way the chosen directory exists is partial-run
                # recovery (same post_id in its post.json) — start clean.
                shutil.rmtree(target_dir)
                log.info("removed partial dump at %s", target_dir)

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
            upserted = manifest.upsert(post, child.slug, post_slug, category.api_id, media_count)
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
