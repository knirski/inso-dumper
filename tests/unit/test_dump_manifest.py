"""Unit tests for the SQLite manifest (dump/manifest.py)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump.manifest import Manifest, open_manifest
from inso_dumper.errors import CliErrorKind
from inso_dumper.models.timeline import Post
from tests.conftest import make_post_dict


def _make_post(post_id: str = "3d5b38b4-6c0b-4e79-8e55-375272c76779") -> Post:
    return Post.model_validate(make_post_dict(id=post_id))


def _upsert(
    m: Manifest,
    post: Post,
    post_slug: str = "2026-08-27-wycieczka",
    dir_name: str = "2026-08-27-wycieczka",
) -> None:
    result = m.upsert(post, "franek-k", post_slug, dir_name, 2, 3)
    assert isinstance(result, Ok)


def test_open_manifest_sets_user_version(tmp_path: Path) -> None:
    path = tmp_path / "announcements" / ".manifest.sqlite"
    result = open_manifest(path)

    assert isinstance(result, Ok)
    m = result.value
    try:
        assert m.schema_version == 3
        assert path.is_file()
    finally:
        m.close()


def test_manifest_file_is_0600(tmp_path: Path) -> None:
    path = tmp_path / "m.sqlite"
    result = open_manifest(path)

    assert isinstance(result, Ok)
    try:
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        result.value.close()


def test_empty_db_recorded_dir_is_none(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        assert m.value.recorded_dir("nonexistent") is None
    finally:
        m.value.close()


def test_upsert_then_recorded_dir(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        post = _make_post()
        _upsert(m.value, post, dir_name="2026-08-27-wycieczka-1")
        assert m.value.recorded_dir(post.id) == "2026-08-27-wycieczka-1"
    finally:
        m.value.close()


def test_upsert_same_post_id_updates_in_place(tmp_path: Path) -> None:
    """post_id is the sole dedup key: a changed post_slug updates the row
    instead of tripping a UNIQUE constraint."""
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        post = _make_post()
        _upsert(m.value, post, post_slug="2026-08-27-wycieczka")
        _upsert(m.value, post, post_slug="2026-08-27-wycieczka-1", dir_name="slugged")

        assert m.value.recorded_dir(post.id) == "slugged"
        conn = m.value._conn
        assert conn is not None
        row = conn.execute(
            "SELECT post_slug, media_count, first_seen_at, last_seen_at"
            " FROM posts WHERE post_id = ?",
            (post.id,),
        ).fetchone()
        assert row[0] == "2026-08-27-wycieczka-1"
        assert row[1] == 3
        assert row[2] <= row[3]
    finally:
        m.value.close()


def test_force_clear_removes_row(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        post = _make_post()
        _upsert(m.value, post)
        result = m.value.force_clear(post.id)

        assert isinstance(result, Ok)
        assert m.value.recorded_dir(post.id) is None
    finally:
        m.value.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    m.value.close()
    m.value.close()


def test_sqlite_error_becomes_internal_err(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    manifest = m.value

    class BrokenConn:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("simulated lock")

        def commit(self) -> None:  # pragma: no cover - never reached
            raise sqlite3.OperationalError("simulated lock")

        def close(self) -> None:
            pass

    monkeypatch.setattr(manifest, "_conn", BrokenConn())
    try:
        result = manifest.upsert(_make_post(), "franek-k", "2026-08-27-wycieczka", "d", 2, 3)
        assert isinstance(result, Err)
        assert result.error.kind is CliErrorKind.INTERNAL
        assert result.error.subject == "manifest_upsert"
    finally:
        manifest.close()


def test_open_manifest_rejects_newer_schema_version(tmp_path: Path) -> None:
    """A manifest written by a newer dumper is a CONFIG error, not silent
    corruption (design: schema mismatch → exit 2)."""
    path = tmp_path / "m.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE posts (post_id TEXT); PRAGMA user_version = 99;")
    conn.commit()
    conn.close()

    result = open_manifest(path)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "manifest_schema"


def test_open_manifest_rejects_v2_manifest(tmp_path: Path) -> None:
    """The v3 gate is loud: an existing v2 manifest (pre-messages dumper)
    is rejected, never silently migrated. Documented recovery: delete the
    .manifest.sqlite file (skip state lost; bytes untouched)."""
    path = tmp_path / "m.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE posts (
            post_id TEXT PRIMARY KEY, post_slug TEXT NOT NULL, dir_name TEXT NOT NULL,
            category_id INTEGER NOT NULL, child_slug TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            media_count INTEGER NOT NULL
        );
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()

    result = open_manifest(path)

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "manifest_schema"


# --- conversations table (v3) --------------------------------------------------


def _upsert_conversation(
    m: Manifest,
    conversation_id: str = "5b8789d6-4b05-11ed-9234-06f545343a70",
    dir_name: str = "2026-08-27-zolta",
    last_update: int = 1787814351,
    message_count: int = 30,
) -> None:
    result = m.upsert_conversation(conversation_id, dir_name, last_update, message_count)
    assert isinstance(result, Ok)


def test_fresh_manifest_creates_both_tables(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        conn = m.value._conn
        assert conn is not None
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"posts", "conversations"} <= tables
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3
    finally:
        m.value.close()


def test_conversation_round_trip(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        _upsert_conversation(m.value)
        recorded = m.value.recorded_conversation("5b8789d6-4b05-11ed-9234-06f545343a70")
        assert recorded is not None
        assert recorded.conversation_id == "5b8789d6-4b05-11ed-9234-06f545343a70"
        assert recorded.dir_name == "2026-08-27-zolta"
        assert recorded.last_update == 1787814351
        assert recorded.message_count == 30
    finally:
        m.value.close()


def test_recorded_conversation_unknown_is_none(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        assert m.value.recorded_conversation("nope") is None
    finally:
        m.value.close()


def test_conversation_upsert_updates_in_place_first_seen_preserved(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        _upsert_conversation(m.value, dir_name="2026-08-27-zolta", last_update=100, message_count=5)
        first = m.value.recorded_conversation("5b8789d6-4b05-11ed-9234-06f545343a70")
        assert first is not None
        _upsert_conversation(m.value, dir_name="2026-08-27-zolta", last_update=200, message_count=9)

        recorded = m.value.recorded_conversation("5b8789d6-4b05-11ed-9234-06f545343a70")
        assert recorded is not None
        assert recorded.last_update == 200
        assert recorded.message_count == 9
        conn = m.value._conn
        assert conn is not None
        row = conn.execute(
            "SELECT first_seen_at, last_seen_at FROM conversations WHERE conversation_id = ?",
            ("5b8789d6-4b05-11ed-9234-06f545343a70",),
        ).fetchone()
        assert row[0] <= row[1]
    finally:
        m.value.close()


def test_force_clear_conversation_removes_row(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        _upsert_conversation(m.value)
        result = m.value.force_clear_conversation("5b8789d6-4b05-11ed-9234-06f545343a70")
        assert isinstance(result, Ok)
        assert m.value.recorded_conversation("5b8789d6-4b05-11ed-9234-06f545343a70") is None
    finally:
        m.value.close()


def test_conversation_broken_conn_is_internal_err(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    manifest = m.value

    class BrokenConn:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("simulated lock")

        def commit(self) -> None:  # pragma: no cover - never reached
            raise sqlite3.OperationalError("simulated lock")

        def close(self) -> None:
            pass

    monkeypatch.setattr(manifest, "_conn", BrokenConn())
    try:
        upsert = manifest.upsert_conversation("c1", "d", 1, 2)
        assert isinstance(upsert, Err)
        assert upsert.error.kind is CliErrorKind.INTERNAL
        assert upsert.error.subject == "manifest_conversation_upsert"
        assert manifest.recorded_conversation("c1") is None  # read swallows, warns
    finally:
        manifest.close()


def test_open_manifest_maps_oserror_to_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkdir failures (permissions, ENOTDIR parents) become Err, not raw
    OSError escaping the boundary."""

    def broken_mkdir(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated denial")

    monkeypatch.setattr(Path, "mkdir", broken_mkdir)
    result = open_manifest(tmp_path / "m.sqlite")

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.INTERNAL
    assert result.error.subject == "manifest_open"
