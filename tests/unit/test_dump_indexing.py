"""Unit tests for the offline dump indexer (Phase 5).

Covers: the pure builders (event/conversation/settlement entries from
parsed payloads), the dump scanner (walk + skip-on-malformed), the HTML
renderers (escaping, relative links, ordering), and the writer's
atomic ``_index.json`` / ``index.html`` output.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Ok
from inso_dumper.dump.indexing import (
    scan_dump,
    write_index,
)
from inso_dumper.models.timeline import Post
from tests._fakes import (
    make_attachment_dict,
    make_photo_dict,
    make_post_dict,
    make_video_dict,
)


def _post(**overrides: Any) -> Post:
    return Post.model_validate(make_post_dict(**overrides))


def _write_post(dump_root: Path, child: str, dir_name: str, post: Post) -> None:
    d = dump_root / child / "announcements" / dir_name
    d.mkdir(parents=True)
    (d / "post.json").write_text(post.model_dump_json(), encoding="utf-8")


def _write_conversation(
    dump_root: Path, dir_name: str, conversation_id: str, count: int, last_ts: int
) -> None:
    d = dump_root / "messages" / dir_name
    d.mkdir(parents=True)
    (d / "conversation.json").write_text(
        json.dumps({"id": conversation_id, "lastUpdate": last_ts, "recipient": {"name": "X"}}),
        encoding="utf-8",
    )
    (d / "messages.json").write_text(
        json.dumps([{"send_timestamp": last_ts - i} for i in range(count)]), encoding="utf-8"
    )


def _write_message_attachment(
    dump_root: Path, dir_name: str, message_id: str, filename: str, data: bytes = b"x"
) -> Path:
    """A message attachment link: flat per-message dir, symlink into a
    plausible _common location (content there is irrelevant here)."""
    blob = dump_root / "_common" / "photos" / "00" / f"{filename}.blob"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    link = dump_root / "messages" / dir_name / "attachments" / message_id / filename
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(blob, link.parent))
    return link


def _write_settlement(dump_root: Path, child: str, month: str, *, invoice: bool) -> None:
    d = dump_root / child / "settlements" / month
    d.mkdir(parents=True)
    payload: dict[str, Any] = {
        "month": month,
        "amountPln": "400.00",
        "saldoPln": "0.00",
        "status": "paid",
    }
    (d / "settlement.json").write_text(json.dumps(payload), encoding="utf-8")
    if invoice:
        (d / "invoice.pdf").write_bytes(b"%PDF-1.4 fake")


def _build_dump(tmp_path: Path) -> Path:
    """Two children with events, one conversation, settlements."""
    _write_post(
        tmp_path,
        "2021-anna-k",
        "2025-09-15-dzien-kolorowy",
        _post(
            title="Dzień Kolorowy",
            createdAt=1757937600,
            media={
                "photos": [
                    make_photo_dict(),
                    make_photo_dict(name="IMG_2.jpeg"),
                    make_video_dict(),
                ],
                "videos": [],
                "attachments": [make_attachment_dict(), make_attachment_dict(name="list.pdf")],
            },
        ),
    )
    _write_post(
        tmp_path,
        "2021-anna-k",
        "2025-09-08-obejscie",
        _post(title="Obejście", createdAt=1757332800),
    )
    _write_post(
        tmp_path,
        "2023-marek-w",
        "2025-09-20-wycieczka",
        _post(title="Wycieczka", createdAt=1758369600),
    )
    _write_conversation(tmp_path, "2026-08-27-zolta", "conv-1", count=3, last_ts=1787814351)
    _write_settlement(tmp_path, "2021-anna-k", "2025-08", invoice=True)
    _write_settlement(tmp_path, "2021-anna-k", "2025-09", invoice=False)
    return tmp_path


def test_scan_dump_collects_events_conversations_settlements(tmp_path: Path) -> None:
    dump_root = _build_dump(tmp_path)
    index = scan_dump(dump_root, log=logging.getLogger("test"))

    assert [c.slug for c in index.children] == ["2021-anna-k", "2023-marek-w"]
    anna = index.children[0]
    # Newest first within a child.
    assert [e.dir for e in anna.events] == [
        "2025-09-15-dzien-kolorowy",
        "2025-09-08-obejscie",
    ]
    kolorowy = anna.events[0]
    assert kolorowy.title == "Dzień Kolorowy"
    assert kolorowy.date == "2025-09-15"
    assert (kolorowy.photos, kolorowy.videos, kolorowy.files) == (2, 1, 2)
    assert anna.events[1].title == "Obejście"

    assert len(index.conversations) == 1
    conv = index.conversations[0]
    assert conv.dir == "2026-08-27-zolta"
    assert conv.messages == 3
    assert conv.last_date == "2026-08-27"

    assert len(index.settlements) == 1
    st = index.settlements[0]
    assert st.child == "2021-anna-k"
    assert (st.months, st.invoices) == (2, 1)

    assert index.generated_at


def test_scan_dump_skips_malformed_entries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_post(tmp_path, "2021-anna-k", "2025-09-15-dobry", _post(title="Dobry"))
    bad = tmp_path / "2021-anna-k" / "announcements" / "2025-09-14-zepsuty"
    bad.mkdir()
    (bad / "post.json").write_text("{not json", encoding="utf-8")
    # A dir with no post.json is ignored silently.
    (tmp_path / "2021-anna-k" / "announcements" / "2025-09-13-pusty").mkdir()

    conv_dir = tmp_path / "messages" / "2026-08-27-zolta"
    conv_dir.mkdir(parents=True)
    (conv_dir / "conversation.json").write_text("{}", encoding="utf-8")
    (conv_dir / "messages.json").write_text("[", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="inso_dumper.dump.indexing"):
        index = scan_dump(tmp_path, log=logging.getLogger("inso_dumper.dump.indexing"))

    assert [e.dir for e in index.children[0].events] == ["2025-09-15-dobry"]
    assert index.conversations == ()
    assert any("post.json" in r.message for r in caplog.records)
    assert any("messages.json" in r.message for r in caplog.records)


def test_scan_dump_ignores_unrelated_directories(tmp_path: Path) -> None:
    (tmp_path / "_common" / "photos" / "ab").mkdir(parents=True)
    (tmp_path / "messages").mkdir()  # empty, no conversations
    index = scan_dump(tmp_path, log=logging.getLogger("test"))
    assert index.children == ()
    assert index.conversations == ()


def test_event_entry_counts_split_photos_videos_files(tmp_path: Path) -> None:
    dump_root = _build_dump(tmp_path)
    index = scan_dump(dump_root, log=logging.getLogger("test"))
    entry = index.children[0].events[0]
    assert (entry.photos, entry.videos, entry.files) == (2, 1, 2)


def test_render_event_html_escapes_and_links_media(tmp_path: Path) -> None:
    dump_root = _build_dump(tmp_path)
    index = scan_dump(dump_root, log=logging.getLogger("test"))
    entry = index.children[0].events[0]
    html = entry.render_html()
    assert "Dzień &lt;Kolorowy&gt;" in html or "Dzień Kolorowy" in html
    assert 'src="photos/1.jpeg"' in html
    assert 'src="videos/1.mp4"' in html
    assert 'href="files/1.pdf"' in html
    assert "<script" not in html


def test_render_event_html_escapes_hostile_title(tmp_path: Path) -> None:
    post = _post(
        title="<script>alert(1)</script> & zło",
        createdAt=1757937600,
        media={
            "photos": [make_photo_dict(name="a&b.jpeg")],
            "videos": [],
            "attachments": [],
        },
    )
    dump_root = tmp_path
    _write_post(dump_root, "c", "2025-09-15-x", post)
    index = scan_dump(dump_root, log=logging.getLogger("test"))
    html = index.children[0].events[0].render_html()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # File names are derived from position + extension only, so the
    # hostile ``a&b.jpeg`` name never reaches the markup.
    assert 'src="photos/1.jpeg"' in html
    assert "a&b" not in html


def test_render_index_html_lists_children_events_messages_settlements(tmp_path: Path) -> None:
    dump_root = _build_dump(tmp_path)
    index = scan_dump(dump_root, log=logging.getLogger("test"))
    html = index.render_html()
    assert "2021-anna-k" in html
    assert 'href="2021-anna-k/announcements/2025-09-15-dzien-kolorowy/index.html"' in html
    assert "2026-08-27-zolta" in html
    assert ("2021-anna-k" in html and "settlements" in html.lower()) or "2025-08" in html


def test_write_index_creates_json_and_pages_idempotently(tmp_path: Path) -> None:
    dump_root = _build_dump(tmp_path)
    log = logging.getLogger("test")
    first = write_index(dump_root, log=log)
    assert isinstance(first, Ok)

    index_json = json.loads((dump_root / "_index.json").read_text(encoding="utf-8"))
    assert index_json["children"][0]["slug"] == "2021-anna-k"
    assert (dump_root / "index.html").exists()
    event_page = (
        dump_root / "2021-anna-k" / "announcements" / "2025-09-15-dzien-kolorowy" / "index.html"
    )
    assert event_page.exists()

    # Idempotent re-run: same content except the timestamp.
    before = event_page.read_text(encoding="utf-8")
    write_index(dump_root, log=log)
    assert event_page.read_text(encoding="utf-8") == before
    index_json2 = json.loads((dump_root / "_index.json").read_text(encoding="utf-8"))
    index_json["generated_at"] = index_json2["generated_at"]
    assert index_json == index_json2


# --- Message attachment galleries --------------------------------------------


def _gallery_dump(tmp_path: Path) -> Path:
    """A dump whose conversation carries mixed message attachments."""
    _write_conversation(tmp_path, "2026-08-27-zolta", "conv-1", count=3, last_ts=1787814351)
    _write_message_attachment(tmp_path, "2026-08-27-zolta", "msg-a", "1.jpeg")
    _write_message_attachment(tmp_path, "2026-08-27-zolta", "msg-a", "2.jpeg")
    _write_message_attachment(tmp_path, "2026-08-27-zolta", "msg-a", "3.mp4")
    _write_message_attachment(tmp_path, "2026-08-27-zolta", "msg-b", "1.pdf")
    return tmp_path


def test_scan_dump_classifies_message_attachments_by_extension(tmp_path: Path) -> None:
    index = scan_dump(_gallery_dump(tmp_path), log=logging.getLogger("test"))
    conv = index.conversations[0]
    assert (conv.photos, conv.videos, conv.files) == (2, 1, 1)
    assert conv.photo_paths == ("attachments/msg-a/1.jpeg", "attachments/msg-a/2.jpeg")
    assert conv.video_paths == ("attachments/msg-a/3.mp4",)
    assert conv.file_paths == ("attachments/msg-b/1.pdf",)


def test_conversation_gallery_groups_by_message_and_escapes(tmp_path: Path) -> None:
    _write_conversation(tmp_path, "conv", "conv-1", count=1, last_ts=1787814351)
    hostile = "<img src=x>"
    _write_message_attachment(tmp_path, "conv", hostile, "1.jpeg")
    index = scan_dump(tmp_path, log=logging.getLogger("test"))
    html = index.conversations[0].render_html()
    assert "<img src=x>" not in html
    assert "&lt;img src=x&gt;" in html
    assert 'src="attachments/&lt;img src=x&gt;/1.jpeg"' in html
    # Message grouping: one section per message id, in stable order.
    two = _gallery_dump(tmp_path)
    html2 = scan_dump(two, log=logging.getLogger("test")).conversations[0].render_html()
    assert html2.index("msg-a") < html2.index("msg-b")
    assert 'src="attachments/msg-a/1.jpeg"' in html2
    assert 'src="attachments/msg-a/3.mp4"' in html2
    assert 'href="attachments/msg-b/1.pdf"' in html2


def test_conversation_without_attachments_has_no_media_and_no_page(tmp_path: Path) -> None:
    _build_dump(tmp_path)  # conversation has no attachments dir
    log = logging.getLogger("test")
    index = scan_dump(tmp_path, log=log)
    conv = index.conversations[0]
    assert (conv.photos, conv.videos, conv.files) == (0, 0, 0)
    top = index.render_html()
    assert 'href="messages/2026-08-27-zolta/index.html"' not in top

    result = write_index(tmp_path, log=log)
    assert isinstance(result, Ok)
    assert not (tmp_path / "messages" / "2026-08-27-zolta" / "index.html").exists()


def test_write_index_writes_conversation_gallery_pages(tmp_path: Path) -> None:
    dump_root = _gallery_dump(tmp_path)
    log = logging.getLogger("test")
    result = write_index(dump_root, log=log)
    assert isinstance(result, Ok)

    page = dump_root / "messages" / "2026-08-27-zolta" / "index.html"
    assert page.exists()
    html = page.read_text(encoding="utf-8")
    assert 'src="attachments/msg-a/1.jpeg"' in html

    index_json = json.loads((dump_root / "_index.json").read_text(encoding="utf-8"))
    conv_json = index_json["conversations"][0]
    assert len(conv_json["photo_paths"]) == 2
    assert conv_json["photo_paths"] == ["attachments/msg-a/1.jpeg", "attachments/msg-a/2.jpeg"]
    assert conv_json["video_paths"] == ["attachments/msg-a/3.mp4"]
    assert conv_json["file_paths"] == ["attachments/msg-b/1.pdf"]

    # Top-level Messages section links to the gallery.
    top = (dump_root / "index.html").read_text(encoding="utf-8")
    assert 'href="messages/2026-08-27-zolta/index.html"' in top

    # Idempotent: identical page on re-run.
    before = page.read_text(encoding="utf-8")
    write_index(dump_root, log=log)
    assert page.read_text(encoding="utf-8") == before


def test_scan_dump_skips_dangling_message_attachments(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_conversation(tmp_path, "conv", "conv-1", count=1, last_ts=1787814351)
    _write_message_attachment(tmp_path, "conv", "msg-a", "1.jpeg")
    dangling = tmp_path / "messages" / "conv" / "attachments" / "msg-a" / "2.jpeg"
    dangling.symlink_to("../missing.jpeg")
    with caplog.at_level(logging.WARNING, logger="inso_dumper.dump.indexing"):
        index = scan_dump(tmp_path, log=logging.getLogger("inso_dumper.dump.indexing"))
    conv = index.conversations[0]
    assert conv.photos == 1
    assert len(caplog.records) >= 1
