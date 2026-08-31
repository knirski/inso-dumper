"""SQLite manifests: idempotent sync state.

The manifest is the shell boundary for the SQLite state DB. Two
locations exist:

- ``dump/<child-slug>/announcements/.manifest.sqlite`` — per-child posts
  state (the ``posts`` table; ``post_id`` is the sole dedup key, a post
  re-appearing across pages or categories updates the row instead of
  tripping a UNIQUE constraint). ``post_slug`` is a plain, indexed
  column used by ``--force``.
- ``dump/messages/.manifest.sqlite`` — account-level conversations
  state (the ``conversations`` table; ``conversation_id`` is the sole
  key). ``last_update`` drives the incremental skip, ``message_count``
  is the tail-fetch ``startingIndex``. Never inside a per-child
  manifest: conversations are account-level data.

All ``sqlite3.Error`` are caught at the boundary and converted to
``Err(CliError(INTERNAL, subject='manifest_<op>'))``; a manifest whose
``user_version`` is not one this code understands (0 or 3) is
``Err(CliError(CONFIG, subject='manifest_schema'))`` — loud, no silent
migration. Documented recovery from a rejected (e.g. v2) manifest:
delete the ``.manifest.sqlite`` file; skip state is lost but dump
output and deduped bytes are untouched.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.timeline import Post

log = logging.getLogger("inso_dumper.dump.manifest")

_SCHEMA_VERSION = 3

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
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    dir_name        TEXT NOT NULL,
    last_update     INTEGER NOT NULL,
    message_count   INTEGER NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """One recorded conversation: everything the skip/tail decision
    needs in a single read."""

    conversation_id: str
    dir_name: str
    last_update: int
    message_count: int


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
        # sqlite creates the file 0644-ish; enforce the invariant.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
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

    def recorded_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """The recorded conversation, or ``None`` when unknown. The read
        side of the conversations skip/tail check; a (essentially
        impossible) query failure reads as "unknown" with a warning."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT conversation_id, dir_name, last_update, message_count"
                " FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        except sqlite3.Error:
            log.warning("conversation manifest read failed for %s", conversation_id)
            return None
        if row is None:
            return None
        return ConversationRecord(
            conversation_id=str(row[0]),
            dir_name=str(row[1]),
            last_update=int(row[2]),
            message_count=int(row[3]),
        )

    def upsert_conversation(
        self, conversation_id: str, dir_name: str, last_update: int, message_count: int
    ) -> CliResult[None]:
        """Insert or update by ``conversation_id`` (sole key);
        ``first_seen_at`` is set on insert and preserved on update."""
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        now = datetime.now(UTC).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO conversations (conversation_id, dir_name, last_update,
                                           message_count, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    dir_name = excluded.dir_name,
                    last_update = excluded.last_update,
                    message_count = excluded.message_count,
                    last_seen_at = excluded.last_seen_at
                """,
                (conversation_id, dir_name, last_update, message_count, now, now),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.warning("conversation manifest upsert failed for %s", conversation_id)
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_conversation_upsert"))
        return Ok(None)

    def force_clear_conversation(self, conversation_id: str) -> CliResult[None]:
        """Remove the conversation row; used by ``--force`` before a
        full re-fetch so an interrupted forced run leaves no stale row."""
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        try:
            self._conn.execute(
                "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
            )
            self._conn.commit()
        except sqlite3.Error:
            return Err(
                CliError(kind=CliErrorKind.INTERNAL, subject="manifest_conversation_force_clear")
            )
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


_ALL = ["ConversationRecord", "Manifest", "open_manifest"]


_SETTLEMENTS_SCHEMA_VERSION = 4

_SETTLEMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settlements (
    month_key             TEXT PRIMARY KEY,
    bill_uuid             TEXT,
    invoice_downloaded_at TEXT,
    first_seen_at         TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    """One recorded settlement month: everything the invoice skip
    decision needs in a single read."""

    month_key: str
    bill_uuid: str | None
    invoice_downloaded_at: str | None


class SettlementManifest:
    """Wrapper owning the settlements manifest ``sqlite3.Connection``.

    A separate class (not a ``Manifest`` mode) because the on-disk
    gate differs: settlement manifests are created at
    ``user_version = 4`` and any other version is rejected loud. The
    existing v3 files are distinct databases and stay untouched.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn: sqlite3.Connection | None = sqlite3.connect(path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SETTLEMENTS_SCHEMA_VERSION):
            self._conn.close()
            raise _SchemaVersionError(f"user_version={version}")
        if version == 0:
            self._conn.executescript(_SETTLEMENTS_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {_SETTLEMENTS_SCHEMA_VERSION}")
            self._conn.commit()
            version = _SETTLEMENTS_SCHEMA_VERSION
        self.schema_version = version

    def recorded_settlement(self, month_key: str) -> SettlementRecord | None:
        """The recorded month, or ``None`` when unknown. The read side
        of the invoice skip check; a (essentially impossible) query
        failure reads as "unknown" with a warning."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT month_key, bill_uuid, invoice_downloaded_at"
                " FROM settlements WHERE month_key = ?",
                (month_key,),
            ).fetchone()
        except sqlite3.Error:
            log.warning("settlements manifest read failed for %s", month_key)
            return None
        if row is None:
            return None
        return SettlementRecord(
            month_key=str(row[0]),
            bill_uuid=str(row[1]) if row[1] is not None else None,
            invoice_downloaded_at=(str(row[2]) if row[2] is not None else None),
        )

    def upsert_settlement(
        self, month_key: str, bill_uuid: str | None, invoice_downloaded_at: str | None
    ) -> CliResult[None]:
        """Insert or update by ``month_key`` (sole key);
        ``first_seen_at`` is set on insert and preserved on update."""
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        now = datetime.now(UTC).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO settlements (month_key, bill_uuid,
                                         invoice_downloaded_at, first_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(month_key) DO UPDATE SET
                    bill_uuid = excluded.bill_uuid,
                    invoice_downloaded_at = excluded.invoice_downloaded_at
                """,
                (month_key, bill_uuid, invoice_downloaded_at, now),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.warning("settlements manifest upsert failed for %s", month_key)
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_settlement_upsert"))
        return Ok(None)

    def force_clear_settlements(self, month_key: str) -> CliResult[None]:
        """Remove the month row; used by ``--force`` before a re-dump
        so an interrupted forced run leaves no stale row."""
        if self._conn is None:
            return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_closed"))
        try:
            self._conn.execute("DELETE FROM settlements WHERE month_key = ?", (month_key,))
            self._conn.commit()
        except sqlite3.Error:
            return Err(
                CliError(kind=CliErrorKind.INTERNAL, subject="manifest_settlement_force_clear")
            )
        return Ok(None)

    def close(self) -> None:
        """Idempotent close."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def open_settlements_manifest(path: Path) -> CliResult[SettlementManifest]:
    """Open (creating in place) the settlements manifest at ``path``."""
    try:
        manifest = SettlementManifest(path)
    except _SchemaVersionError:
        log.warning(
            "settlements manifest at %s has an incompatible schema version;"
            " documented recovery: delete the file (dump output is untouched)",
            path,
        )
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="manifest_schema"))
    except sqlite3.Error:
        log.warning("cannot open settlements manifest at %s", path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_open"))
    except OSError:
        log.warning("cannot create settlements manifest location at %s", path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="manifest_open"))
    return Ok(manifest)


__all__ = [
    "ConversationRecord",
    "Manifest",
    "SettlementManifest",
    "SettlementRecord",
    "open_manifest",
    "open_settlements_manifest",
]
