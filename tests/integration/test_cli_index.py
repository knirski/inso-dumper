"""Integration tests for the ``index`` CLI command.

The command is offline (no session, no network): it rebuilds
``_index.json`` and the HTML pages from a dump tree built in
``tmp_path``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import typer.testing

from inso_dumper.cli import app
from inso_dumper.models.timeline import Post
from tests._fakes import make_post_dict


@pytest.fixture
def cli_runner() -> typer.testing.CliRunner:
    return typer.testing.CliRunner()


def _write_post(dump_root: Path, child: str, dir_name: str, post: Post) -> None:
    d = dump_root / child / "announcements" / dir_name
    d.mkdir(parents=True)
    (d / "post.json").write_text(post.model_dump_json(), encoding="utf-8")


def _build_dump(tmp_path: Path) -> Path:
    _write_post(
        tmp_path,
        "2021-anna-k",
        "2025-09-15-dzien-kolorowy",
        Post.model_validate(make_post_dict(title="Dzień Kolorowy", createdAt=1757937600)),
    )
    conv = tmp_path / "messages" / "2026-08-27-zolta"
    conv.mkdir(parents=True)
    (conv / "conversation.json").write_text("{}", encoding="utf-8")
    (conv / "messages.json").write_text(
        json.dumps([{"send_timestamp": 1787814351}]), encoding="utf-8"
    )
    # A message attachment gallery: one photo for one message.
    blob = tmp_path / "_common" / "photos" / "00" / "blob"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"jpeg")
    link = conv / "attachments" / "msg-1" / "1.jpeg"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(blob, link.parent))
    st = tmp_path / "2021-anna-k" / "settlements" / "2025-09"
    st.mkdir(parents=True)
    (st / "settlement.json").write_text(
        json.dumps(
            {"month": "2025-09", "amountPln": "400.00", "saldoPln": "0.00", "status": "paid"}
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_index_command_writes_json_and_pages(
    tmp_path: Path, cli_runner: typer.testing.CliRunner
) -> None:
    dump_root = _build_dump(tmp_path)
    result = cli_runner.invoke(app, ["index", "--dump-root", str(dump_root)])
    assert result.exit_code == 0, result.output
    assert "1 events" in result.output

    index_json = json.loads((dump_root / "_index.json").read_text(encoding="utf-8"))
    assert index_json["children"][0]["slug"] == "2021-anna-k"
    assert index_json["children"][0]["events"][0]["title"] == "Dzień Kolorowy"
    assert index_json["conversations"][0]["messages"] == 1
    assert index_json["settlements"][0]["months"] == 1

    top = (dump_root / "index.html").read_text(encoding="utf-8")
    assert "2021-anna-k/announcements/2025-09-15-dzien-kolorowy/index.html" in top
    event_page = (
        dump_root / "2021-anna-k" / "announcements" / "2025-09-15-dzien-kolorowy" / "index.html"
    )
    assert "Dzień Kolorowy" in event_page.read_text(encoding="utf-8")

    # Message attachment gallery: page written and linked from the top.
    gallery = dump_root / "messages" / "2026-08-27-zolta" / "index.html"
    assert gallery.exists()
    assert 'src="attachments/msg-1/1.jpeg"' in gallery.read_text(encoding="utf-8")
    assert 'href="messages/2026-08-27-zolta/index.html"' in top


def test_index_command_is_idempotent(tmp_path: Path, cli_runner: typer.testing.CliRunner) -> None:
    dump_root = _build_dump(tmp_path)
    args = ["index", "--dump-root", str(dump_root)]
    assert cli_runner.invoke(app, args).exit_code == 0
    first = (dump_root / "index.html").read_text(encoding="utf-8")
    assert cli_runner.invoke(app, args).exit_code == 0
    assert (dump_root / "index.html").read_text(encoding="utf-8") == first


def test_index_command_on_missing_dump_root_creates_empty_index(
    tmp_path: Path, cli_runner: typer.testing.CliRunner
) -> None:
    dump_root = tmp_path / "fresh"
    result = cli_runner.invoke(app, ["index", "--dump-root", str(dump_root)])
    assert result.exit_code == 0
    index_json = json.loads((dump_root / "_index.json").read_text(encoding="utf-8"))
    assert index_json["children"] == []
    assert "0 events" in result.output
