"""Per-child SQLite manifest: idempotent sync state.

The manifest is the shell boundary for the SQLite state DB at
``dump/<child-slug>/announcements/.manifest.sqlite``. One table,
``posts``; ``post_id`` is the sole dedup key (a post re-appearing
across pages or categories updates the row instead of tripping a
UNIQUE constraint). ``post_slug`` is a plain, indexed column used by
``--force``.

All ``sqlite3.Error`` are caught at the boundary and converted to
``Err(CliError(INTERNAL, subject='manifest_<op>'))``; a manifest whose
``user_version`` is newer than this code understands is
``Err(CliError(CONFIG, subject='manifest_schema'))`` — the user's data
needs a newer dumper, not silent corruption.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.timeline import Post

log = logging.getLogger("inso_dumper.dump.manifest")

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id       TEXT PRIMARY KEY,
    post_slug     TEXT NOT NULL,
    dir_name      TEXT NOT NULL,
    category_id   INTEGER NOT NULL,
    child_slug    TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    media_count   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_by_slug ON posts (post_slug);
"""


class _SchemaVersionError(Exception):
    """Raised by ``Manifest`` when the on-disk ``user_version`` is newer
    than this code understands. ``open_manifest`` maps it to
    ``Err(CONFIG, 'manifest_schema')``; bypassing ``open_manifest`` and
    constructing ``Manifest`` directly is a programmer error."""


class Manifest:
    """Wrapper owning the ``sqlite3.Connection`` lifecycle."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn: sqlite3.Connection | None = sqlite3.connect(path)
        try:
            # sqlite creates the file 0644-ish; enforce the invariant.
            os.chmod(path, 0o600)
        except OSError:
            pass
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SCHEMA_VERSION):
            self._conn.close()
            raise _SchemaVersionError(f"user_version={version}")
        if version == 0:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()
            version = _SCHEMA_VERSION
        self.schema_version = version

    def recorded_dir(self, post_id: str) -> str | None:
        """The directory name the post was actually dumped into (may be
        collision-suffixed), or ``None`` when unknown. The read side of
        the skip check. A (post-validation, essentially impossible)
        query failure reads as "not dumped" — the idempotent re-dump
        that follows overwrites deterministically — with a warning."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT dir_name FROM posts WHERE post_id = ?", (post_id,)
            ).fetchone()
        except sqlite3.Error:
            log.warning("manifest read failed; treating %s as not dumped", post_id)
            return None
        return str(row[0]) if row is not None else None

    def upsert(
        self,
        post: Post,
        child_slug: str,
        post_slug: str,
        dir_name: str,
        category_id: int,
        media_count: int,
    ) -> CliResult[None]:
        """The write side: insert or update by ``post_id`` (sole key).

        ``dir_name`` records the actual (possibly collision-suffixed)
        directory so re-runs can find the dump.
        """
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        now = datetime.now(UTC).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO posts (post_id, post_slug, dir_name, category_id,
                                   child_slug, first_seen_at, last_seen_at,
                                   media_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    post_slug = excluded.post_slug,
                    dir_name = excluded.dir_name,
                    category_id = excluded.category_id,
                    last_seen_at = excluded.last_seen_at,
                    media_count = excluded.media_count
                """,
                (post.id, post_slug, dir_name, category_id, child_slug, now, now, media_count),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.warning("manifest upsert failed for %s", post.id)
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_upsert"))
        return Ok(None)

    def force_clear(self, post_id: str) -> CliResult[None]:
        """Remove the row for ``post_id``; used by ``--force`` before a
        re-dump so an interrupted forced run leaves no stale row."""
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        try:
            self._conn.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))
            self._conn.commit()
        except sqlite3.Error:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_force_clear"))
        return Ok(None)

    def close(self) -> None:
        """Idempotent close."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def open_manifest(path: Path) -> CliResult[Manifest]:
    """Open (creating or migrating in place) the manifest at ``path``."""
    try:
        manifest = Manifest(path)
    except _SchemaVersionError:
        log.warning("manifest at %s has a newer schema version", path)
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="manifest_schema"))
    except sqlite3.Error:
        log.warning("cannot open manifest at %s", path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_open"))
    except OSError:
        log.warning("cannot create manifest location at %s", path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_open"))
    return Ok(manifest)


__all__ = ["Manifest", "open_manifest"]
