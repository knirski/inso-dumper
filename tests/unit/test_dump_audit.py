"""Unit tests for the offline dump audit commands (Phase 6).

Covers: ``verify_dump`` (blob re-hashing against content-addressed
paths, dangling-symlink detection, unexpected-file warnings) and
``materialize_dump`` (symlink → real copy, idempotency, ``_common/``
untouched, dangling links skipped).
"""

import logging
import os
import stat
from pathlib import Path

import pytest

from inso_dumper._result import Ok
from inso_dumper.dump.audit import materialize_dump, verify_dump
from inso_dumper.dump.layout import sha256_hex

log = logging.getLogger("test")


def _blob(dump_root: Path, kind: str, data: bytes, ext: str) -> Path:
    """A correctly content-addressed blob (filename stem = sha256)."""
    hash_hex = sha256_hex(data)
    path = dump_root / "_common" / kind / hash_hex[:2] / f"{hash_hex}{ext}"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return path


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target, link.parent))


def _build_tree(tmp_path: Path) -> Path:
    """One blob, one post photo link, one message attachment link."""
    blob = _blob(tmp_path, "photos", b"jpeg-bytes-1", ".jpeg")
    _link(
        tmp_path / "c" / "announcements" / "2025-09-15-x" / "photos" / "1.jpeg",
        blob,
    )
    msg_blob = _blob(tmp_path, "attachments", b"%PDF-1.4", ".pdf")
    _link(
        tmp_path / "messages" / "2026-08-27-x" / "attachments" / "m1" / "1.pdf",
        msg_blob,
    )
    return tmp_path


def test_verify_healthy_tree_reports_zero_findings(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    result = verify_dump(tmp_path, log=log)
    assert isinstance(result, Ok)
    report = result.value
    assert report.blobs == 2
    assert report.corrupt == ()
    assert report.unexpected == ()
    assert report.links == 2
    assert report.dangling == ()


def test_verify_detects_corrupted_blob(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    # Flip bytes under a valid content-addressed name.
    h = sha256_hex(b"jpeg-bytes-1")
    bad = tmp_path / "_common" / "photos" / h[:2] / f"{h}.jpeg"
    bad.write_bytes(b"tampered!")
    result = verify_dump(tmp_path, log=log)
    assert isinstance(result, Ok)
    report = result.value
    assert report.blobs == 2
    assert len(report.corrupt) == 1
    assert report.corrupt[0].endswith(f"{h}.jpeg")


def test_verify_flags_dangling_link_and_unexpected_common_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _build_tree(tmp_path)
    h = sha256_hex(b"jpeg-bytes-1")
    (tmp_path / "_common" / "photos" / h[:2] / "leftover.tmp").write_bytes(b"x")
    dangling = tmp_path / "c" / "announcements" / "2025-09-15-x" / "photos" / "2.jpeg"
    dangling.symlink_to("../missing-blob.jpeg")
    with caplog.at_level(logging.WARNING):
        result = verify_dump(tmp_path, log=log)
    assert isinstance(result, Ok)
    report = result.value
    assert report.links == 3
    assert len(report.dangling) == 1
    assert report.unexpected == (f"_common/photos/{h[:2]}/leftover.tmp",)
    assert any("leftover.tmp" in r.message for r in caplog.records)


def test_verify_missing_blob_root_is_clean_empty_report(tmp_path: Path) -> None:
    (tmp_path / "c").mkdir()
    result = verify_dump(tmp_path, log=log)
    assert isinstance(result, Ok)
    report = result.value
    assert report.blobs == 0
    assert report.links == 0
    assert report.corrupt == ()
    assert report.dangling == ()


def test_materialize_replaces_symlinks_with_real_copies(tmp_path: Path) -> None:
    dump_root = _build_tree(tmp_path)
    link = dump_root / "c" / "announcements" / "2025-09-15-x" / "photos" / "1.jpeg"
    result = materialize_dump(dump_root, log=log)
    assert isinstance(result, Ok)
    assert result.value.copied == 2
    assert result.value.skipped_dangling == 0

    assert not link.is_symlink()
    assert link.read_bytes() == b"jpeg-bytes-1"
    assert not stat.S_ISLNK(link.lstat().st_mode)
    assert (link.stat().st_mode & 0o777) == 0o600
    # Blob store untouched.
    h = sha256_hex(b"jpeg-bytes-1")
    blob = dump_root / "_common" / "photos" / h[:2] / f"{h}.jpeg"
    assert blob.read_bytes() == b"jpeg-bytes-1"


def test_materialize_is_idempotent_and_skips_dangling(tmp_path: Path) -> None:
    dump_root = _build_tree(tmp_path)
    dangling = dump_root / "messages" / "2026-08-27-x" / "attachments" / "m1" / "2.pdf"
    dangling.symlink_to("../missing-blob.pdf")
    assert isinstance(materialize_dump(dump_root, log=log), Ok)
    rerun = materialize_dump(dump_root, log=log)
    assert isinstance(rerun, Ok)
    first = rerun.value
    assert first.copied == 0
    assert first.skipped_dangling == 1
    assert dangling.is_symlink()  # left in place for the human to decide
