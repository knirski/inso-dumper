"""Offline dump indexer: ``_index.json`` + HTML pages (Phase 5).

The index is rebuilt from the dump tree itself — never from the sync
manifests (they do not store titles/dates) — so it works offline,
is idempotent, and survives ``--force`` re-dumps and ``materialize``.
One bad file on disk is skipped with a warning, never a failed run:
the index must not be held hostage by a single truncated directory.

Pure part: the entry dataclasses and their ``render_html`` methods
(string building only, ``html.escape`` everywhere). Shell part:
``scan_dump`` (read-only walk) and ``write_index`` (atomic writes via
the shared ``_write_bytes``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import assert_never

from inso_dumper._result import Err, Ok
from inso_dumper.dump.writer import _write_bytes, ensure_private_dir
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.timeline import Photo, Post, Video

log = logging.getLogger("inso_dumper.dump.indexing")


@dataclass(frozen=True, slots=True)
class EventEntry:
    """One announcement/gallery event directory."""

    child: str
    dir: str
    date: str  # ISO date from the directory prefix
    title: str
    photo_paths: tuple[str, ...]  # relative to the event dir, disk-exact
    video_paths: tuple[str, ...]
    file_paths: tuple[tuple[str, str], ...]  # (path, original name)

    @property
    def photos(self) -> int:
        return len(self.photo_paths)

    @property
    def videos(self) -> int:
        return len(self.video_paths)

    @property
    def files(self) -> int:
        return len(self.file_paths)

    def render_html(self) -> str:
        """The per-event gallery page; links use the same relative
        paths ``sync`` wrote, so no filenames are guessed here."""
        parts: list[str] = [
            "<!doctype html>",
            '<html lang="pl"><head><meta charset="utf-8">',
            f"<title>{escape(self.title)}</title></head><body>",
            f"<h1>{escape(self.title)}</h1>",
            f"<p>{escape(self.date)}",
            f" · {self.photos} photos · {self.videos} videos · {self.files} files</p>",
        ]
        for path in self.photo_paths:
            parts.append(f'<p><img src="{escape(path, quote=True)}" alt=""></p>')
        for path in self.video_paths:
            parts.append(f'<p><video controls src="{escape(path, quote=True)}"></video></p>')
        if self.file_paths:
            parts.append("<ul>")
            for path, name in self.file_paths:
                parts.append(f'<li><a href="{escape(path, quote=True)}">{escape(name)}</a></li>')
            parts.append("</ul>")
        parts.append("</body></html>")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class ChildEntry:
    slug: str
    events: tuple[EventEntry, ...]  # newest first


@dataclass(frozen=True, slots=True)
class ConversationEntry:
    dir: str
    messages: int
    last_date: str  # ISO date of the newest message


@dataclass(frozen=True, slots=True)
class SettlementEntry:
    child: str
    months: int
    invoices: int  # month dirs with an invoice.pdf on disk


@dataclass(frozen=True, slots=True)
class DumpIndex:
    generated_at: str  # UTC ISO timestamp
    children: tuple[ChildEntry, ...]  # slug order
    conversations: tuple[ConversationEntry, ...]  # dir order
    settlements: tuple[SettlementEntry, ...]  # child order

    def render_html(self) -> str:
        """The top-level page: children → events, then the shallow
        messages/settlements sections."""
        parts: list[str] = [
            "<!doctype html>",
            '<html lang="pl"><head><meta charset="utf-8">',
            "<title>inso-dumper</title></head><body>",
            f"<h1>inso-dumper</h1><p>generated {escape(self.generated_at)}</p>",
        ]
        for child in self.children:
            parts.append(f"<h2>{escape(child.slug)}</h2>")
            if not child.events:
                parts.append("<p>no events</p>")
                continue
            parts.append("<ul>")
            for event in child.events:
                href = f"{escape(child.slug)}/announcements/{escape(event.dir)}/index.html"
                label = f"{escape(event.date)} — {escape(event.title)}"
                counts = f"({event.photos} photos, {event.videos} videos, {event.files} files)"
                parts.append(f'<li><a href="{href}">{label}</a> {counts}</li>')
            parts.append("</ul>")
        parts.append("<h2>Messages</h2><ul>")
        for conv in self.conversations:
            parts.append(
                f"<li>{escape(conv.dir)} — {conv.messages} messages, last {escape(conv.last_date)}</li>"
            )
        parts.append("</ul><h2>Settlements</h2><ul>")
        for st in self.settlements:
            parts.append(
                f"<li>{escape(st.child)} — {st.months} months, {st.invoices} invoices</li>"
            )
        parts.append("</ul></body></html>")
        return "\n".join(parts)


def event_entry(child_slug: str, dir_name: str, post: Post) -> EventEntry:
    """Derive an index entry from a parsed ``post.json``. Media file
    names mirror ``sync``'s per-kind counters exactly (photos and
    videos number independently inside ``media.photos``; attachments
    follow), so links always match the files on disk."""
    photo_paths: list[str] = []
    video_paths: list[str] = []
    files: list[tuple[str, str]] = []
    for media in post.media.photos:
        match media:
            case Photo():
                photo_paths.append(f"photos/{len(photo_paths) + 1}{media.ext}")
            case Video():
                video_paths.append(f"videos/{len(video_paths) + 1}{media.ext}")
            case _ as unreachable:
                assert_never(unreachable)
    files = [
        (f"files/{i + 1}{attachment.ext}", attachment.name)
        for i, attachment in enumerate(post.media.attachments)
    ]
    return EventEntry(
        child=child_slug,
        dir=dir_name,
        date=post.created_at.date().isoformat(),
        title=post.title,
        photo_paths=tuple(photo_paths),
        video_paths=tuple(video_paths),
        file_paths=tuple(files),
    )


def scan_dump(dump_root: Path, *, log: logging.Logger) -> DumpIndex:
    """Walk the dump tree read-only and build the index. Malformed or
    unreadable per-item JSON is skipped with a warning."""
    children: list[ChildEntry] = []
    for child_dir in sorted(p for p in dump_root.iterdir() if p.is_dir()):
        announcements = child_dir / "announcements"
        if not announcements.is_dir():
            continue
        events: list[EventEntry] = []
        for event_dir in sorted(announcements.iterdir()):
            post_json = event_dir / "post.json"
            if not post_json.is_file():
                continue
            try:
                post = Post.model_validate_json(post_json.read_bytes())
            except Exception:
                log.warning("skipping %s: unreadable post.json", event_dir)
                continue
            events.append(event_entry(child_dir.name, event_dir.name, post))
        events.sort(key=lambda e: e.date, reverse=True)
        children.append(ChildEntry(slug=child_dir.name, events=tuple(events)))

    conversations: list[ConversationEntry] = []
    messages_root = dump_root / "messages"
    if messages_root.is_dir():
        for conv_dir in sorted(p for p in messages_root.iterdir() if p.is_dir()):
            try:
                messages = json.loads((conv_dir / "messages.json").read_bytes())
                count = len(messages)
                # messages.json is serialized with Python field names.
                last_ts = max(int(m["send_timestamp"]) for m in messages)
            except OSError, ValueError, TypeError, KeyError:
                log.warning("skipping %s: unreadable messages.json", conv_dir)
                continue
            last_date = datetime.fromtimestamp(last_ts, UTC).date().isoformat()
            conversations.append(
                ConversationEntry(dir=conv_dir.name, messages=count, last_date=last_date)
            )

    settlements: list[SettlementEntry] = []
    for child_dir in sorted(p for p in dump_root.iterdir() if p.is_dir()):
        st_root = child_dir / "settlements"
        if not st_root.is_dir():
            continue
        months = [p for p in sorted(st_root.iterdir()) if (p / "settlement.json").is_file()]
        invoices = [p for p in months if (p / "invoice.pdf").is_file()]
        settlements.append(
            SettlementEntry(child=child_dir.name, months=len(months), invoices=len(invoices))
        )

    return DumpIndex(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        children=tuple(children),
        conversations=tuple(conversations),
        settlements=tuple(settlements),
    )


def write_index(dump_root: Path, *, log: logging.Logger) -> CliResult[DumpIndex]:
    """Rebuild ``_index.json``, the top-level ``index.html``, and every
    event's ``index.html``. Idempotent; ``OSError`` is
    ``Err(INTERNAL, 'index_write')``."""
    index = scan_dump(dump_root, log=log)
    payload = json.dumps(asdict(index), indent=2, ensure_ascii=False).encode("utf-8")
    try:
        ensure_private_dir(dump_root)
        _write_bytes(dump_root / "_index.json", payload)
        _write_bytes(dump_root / "index.html", index.render_html().encode("utf-8"))
        for child in index.children:
            for event in child.events:
                event_dir = dump_root / child.slug / "announcements" / event.dir
                ensure_private_dir(event_dir)
                _write_bytes(event_dir / "index.html", event.render_html().encode("utf-8"))
    except OSError:
        log.warning("index write failed under %s", dump_root)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="index_write"))
    log.info(
        "indexed %d events, %d conversations, %d settlement children",
        sum(len(c.events) for c in index.children),
        len(index.conversations),
        len(index.settlements),
    )
    return Ok(index)


__all__ = [
    "ChildEntry",
    "ConversationEntry",
    "DumpIndex",
    "EventEntry",
    "SettlementEntry",
    "event_entry",
    "scan_dump",
    "write_index",
]
