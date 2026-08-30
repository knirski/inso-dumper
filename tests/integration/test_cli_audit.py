"""Integration tests for the ``verify`` and ``materialize`` CLI commands.

Both commands are offline: they audit/rewrite a dump tree built in
``tmp_path``. ``verify`` exits 0 clean and 1 with findings; writes go
through ``materialize`` only.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import typer.testing

from inso_dumper.cli import app
from inso_dumper.dump.layout import sha256_hex


@pytest.fixture
def cli_runner() -> typer.testing.CliRunner:
    return typer.testing.CliRunner()


def _build_tree(tmp_path: Path) -> Path:
    data = b"jpeg-bytes"
    h = sha256_hex(data)
    blob = tmp_path / "_common" / "photos" / h[:2] / f"{h}.jpeg"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    link = tmp_path / "c" / "announcements" / "2025-09-15-x" / "photos" / "1.jpeg"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(blob, link.parent))
    return tmp_path


def test_verify_clean_tree_exits_zero(tmp_path: Path, cli_runner: typer.testing.CliRunner) -> None:
    dump_root = _build_tree(tmp_path)
    result = cli_runner.invoke(app, ["verify", "--dump-root", str(dump_root)])
    assert result.exit_code == 0, result.output
    assert "1 blobs (0 corrupt)" in result.output
    assert "1 links (0 dangling)" in result.output


def test_verify_corrupt_blob_exits_one(tmp_path: Path, cli_runner: typer.testing.CliRunner) -> None:
    dump_root = _build_tree(tmp_path)
    h = sha256_hex(b"jpeg-bytes")
    (dump_root / "_common" / "photos" / h[:2] / f"{h}.jpeg").write_bytes(b"tampered")
    result = cli_runner.invoke(app, ["verify", "--dump-root", str(dump_root)])
    assert result.exit_code == 1
    assert "corrupt" in result.output


def test_materialize_makes_tree_self_contained(
    tmp_path: Path, cli_runner: typer.testing.CliRunner
) -> None:
    dump_root = _build_tree(tmp_path)
    link = dump_root / "c" / "announcements" / "2025-09-15-x" / "photos" / "1.jpeg"
    result = cli_runner.invoke(app, ["materialize", "--dump-root", str(dump_root)])
    assert result.exit_code == 0, result.output
    assert "Materialized 1 links" in result.output
    assert not link.is_symlink()
    assert link.read_bytes() == b"jpeg-bytes"

    # Re-run: nothing left to copy.
    rerun = cli_runner.invoke(app, ["materialize", "--dump-root", str(dump_root)])
    assert rerun.exit_code == 0
    assert "Materialized 0 links" in rerun.output


def test_materialize_preserves_index_and_json(
    tmp_path: Path, cli_runner: typer.testing.CliRunner
) -> None:
    dump_root = _build_tree(tmp_path)
    (dump_root / "c" / "announcements" / "2025-09-15-x" / "post.json").write_text(
        json.dumps({"id": "p1"}), encoding="utf-8"
    )
    assert cli_runner.invoke(app, ["materialize", "--dump-root", str(dump_root)]).exit_code == 0
    post_json = dump_root / "c" / "announcements" / "2025-09-15-x" / "post.json"
    assert json.loads(post_json.read_text(encoding="utf-8")) == {"id": "p1"}
    # The store itself stays (future syncs dedup against it).
    h = sha256_hex(b"jpeg-bytes")
    assert (dump_root / "_common" / "photos" / h[:2] / f"{h}.jpeg").exists()
