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


def _upsert(m: Manifest, post: Post, post_slug: str = "2026-08-27-wycieczka") -> None:
    result = m.upsert(post, "franek-k", post_slug, 2, 3)
    assert isinstance(result, Ok)


def test_open_manifest_sets_user_version(tmp_path: Path) -> None:
    path = tmp_path / "announcements" / ".manifest.sqlite"
    result = open_manifest(path)

    assert isinstance(result, Ok)
    m = result.value
    try:
        assert m.schema_version == 1
        assert path.is_file()
    finally:
        m.close()


def test_empty_db_already_dumped_is_false(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        assert m.value.already_dumped("nonexistent") is False
    finally:
        m.value.close()


def test_upsert_then_already_dumped(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        post = _make_post()
        _upsert(m.value, post)
        assert m.value.already_dumped(post.id) is True
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
        _upsert(m.value, post, post_slug="2026-08-27-wycieczka-1")

        assert m.value.already_dumped(post.id) is True
        row = m.value.get(post.id)
        assert row is not None
        assert row["post_slug"] == "2026-08-27-wycieczka-1"
        assert row["media_count"] == 3
        assert str(row["first_seen_at"]) <= str(row["last_seen_at"])
    finally:
        m.value.close()


def test_force_clear_removes_row(tmp_path: Path) -> None:
    m = open_manifest(tmp_path / "m.sqlite")
    assert isinstance(m, Ok)
    try:
        post = _make_post()
        _upsert(m.value, post)
        result = m.value.force_clear("2026-08-27-wycieczka")

        assert isinstance(result, Ok)
        assert m.value.already_dumped(post.id) is False
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
        result = manifest.upsert(_make_post(), "franek-k", "2026-08-27-wycieczka", 2, 3)
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
