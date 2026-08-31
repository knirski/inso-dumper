"""Disk boundary: atomic writes with enforced modes, symlink-or-copy.

The writer is the shell boundary for the dump tree. Every write is
atomic (``*.tmp`` + ``fsync`` + ``os.replace``), files are ``0o600``
and directories ``0o700`` (children's personal data). ``OSError`` is
caught here and converted to ``Err(CliError(INTERNAL, subject))`` —
the core never sees an exception from this module.

Symlinks are relative and point into the ``_common/`` shared store;
filesystems that reject symlinks get a plain copy (logged at INFO).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar, assert_never

from inso_dumper._result import Err, Ok
from inso_dumper.dump.layout import dedup_target, post_target_dir, sha256_hex
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.timeline import MediaItemKind, Post

log = logging.getLogger("inso_dumper.dump.writer")

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _write_bytes(path: Path, data: bytes, mode: int = _FILE_MODE) -> None:
    """Atomic, fsync'd write with a restrictive mode. Raises ``OSError``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode=mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class _HtmlToMarkdown(HTMLParser):
    """A small, deliberately best-effort HTML → Markdown converter.

    Keeps block structure (p, headings, lists, blockquote) and inline
    emphasis/links; strips styles, scripts, and unknown tags while
    keeping their text. Per the spec it never raises — unknown
    structures degrade to plain text and the raw ``post.html`` is
    always present as the source of truth.
    """

    _BLOCK_TAGS: ClassVar[set[str]] = {
        "p",
        "ul",
        "ol",
        "li",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
    _INLINE_PREFIX: ClassVar[dict[str, str]] = {
        "b": "**",
        "strong": "**",
        "i": "*",
        "em": "*",
        "code": "`",
    }
    _HEADINGS: ClassVar[dict[str, str]] = {
        "h1": "# ",
        "h2": "## ",
        "h3": "### ",
        "h4": "#### ",
        "h5": "##### ",
        "h6": "###### ",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._inline_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in self._HEADINGS:
            self._parts.append("\n\n" + self._HEADINGS[tag])
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._parts.append("[")
            self._inline_tags.append("](" + href + ")")
        elif tag in self._INLINE_PREFIX:
            self._parts.append(self._INLINE_PREFIX[tag])
            self._inline_tags.append(self._INLINE_PREFIX[tag])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._inline_tags:
            self._parts.append(self._inline_tags.pop())
        elif tag in self._INLINE_PREFIX and self._inline_tags:
            self._inline_tags.pop()
            self._parts.append(self._INLINE_PREFIX[tag])
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def markdown(self) -> str:
        text = "".join(self._parts)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip() + "\n"


def _to_markdown(html: str) -> str:
    converter = _HtmlToMarkdown()
    converter.feed(html)
    return converter.markdown()


def _post_dir_kind(kind: MediaItemKind) -> str:
    """The per-post directory for a media kind (platform naming: files/)."""
    match kind:
        case MediaItemKind.PHOTO:
            return "photos"
        case MediaItemKind.VIDEO:
            return "videos"
        case MediaItemKind.ATTACHMENT:
            return "files"
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class _PostFile:
    name: str
    data: bytes


def ensure_private_dir(path: Path, *, parents: bool = False) -> None:
    """Create ``path`` and enforce ``0o700``.

    With ``parents=False`` only the leaf is created (callers that need a
    fully-tightened chain create each level). ``mkdir(mode=...)`` only
    applies at creation and is umask-masked, so the explicit ``chmod``
    is what actually enforces the invariant on pre-existing directories.
    Raises ``OSError``.
    """
    path.mkdir(parents=parents, exist_ok=True, mode=_DIR_MODE)
    os.chmod(path, _DIR_MODE)


def _is_same_post_on_disk(target_dir: Path, post: Post) -> bool:
    """True if ``target_dir`` holds a ``post.json`` naming the same post.

    That means a partial dump from an interrupted run (the manifest was
    never upserted) — recoverable by re-dumping in place. Anything
    unreadable, non-UTF-8, or non-object reads as "not the same post".
    """
    try:
        data = json.loads((target_dir / "post.json").read_bytes())
        return isinstance(data, dict) and data.get("id") == post.id
    except OSError, ValueError:
        # ValueError covers JSONDecodeError and non-UTF-8 bytes.
        return False


def resolve_post_dir(
    dump_root: Path, child_slug: str, post: Post, *, log: logging.Logger
) -> CliResult[Path]:
    """Pick, clean, and return the destination directory for ``post``.

    - fresh path → use it;
    - existing dir whose ``post.json`` names the same ``post_id`` → a
      partial dump from an interrupted run; remove it and re-dump in
      place (no orphaned ``-N`` directories);
    - existing dir with a different/unreadable ``post.json`` → slug
      collision; append the next ``-N`` suffix.

    All disk policy for post placement lives here (the SDD assigns it
    to the writer); the orchestrator only consumes the returned path.
    """
    base = post_target_dir(dump_root, child_slug, post)
    try:
        if not base.exists() or _is_same_post_on_disk(base, post):
            chosen = base
        else:
            n = 1
            while True:
                candidate = base.parent / f"{base.name}-{n}"
                if not candidate.exists() or _is_same_post_on_disk(candidate, post):
                    chosen = candidate
                    break
                n += 1
        if chosen.exists():
            shutil.rmtree(chosen)
            log.info("removed partial dump at %s", chosen)
        ensure_private_dir(chosen)
    except OSError:
        log.warning("cannot prepare post dir under %s", base)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="post_dir_prepare"))
    return Ok(chosen)


def write_post(post: Post, target_dir: Path, *, log: logging.Logger) -> CliResult[Path]:
    """Write the per-post directory atomically: ``post.json``, then
    ``post.html``, then ``post.md`` (a best-effort HTML→Markdown
    conversion — the converter itself never fails, so all three files
    are written or none are). Any ``OSError`` removes the directory and
    returns ``Err(INTERNAL)`` — no partial dumps.
    """
    stamped = post.model_copy(update={"dumped_at": datetime.now(UTC)})
    files: list[_PostFile] = [
        _PostFile("post.json", stamped.model_dump_json().encode("utf-8")),
        _PostFile("post.html", post.content.encode("utf-8")),
    ]
    files.append(_PostFile("post.md", _to_markdown(post.content).encode("utf-8")))
    try:
        ensure_private_dir(target_dir, parents=True)
        for f in files:
            _write_bytes(target_dir / f.name, f.data, _FILE_MODE)
            log.info("wrote %s", target_dir / f.name)
    except OSError:
        log.warning("removing partial post dir %s", target_dir)
        shutil.rmtree(target_dir, ignore_errors=True)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="post_write"))
    return Ok(target_dir)


def write_deduped_media(
    dump_root: Path,
    hash_hex: str,
    ext: str,
    kind: MediaItemKind,
    data: bytes,
    *,
    log: logging.Logger,
) -> CliResult[Path]:
    """Store media bytes in ``_common/`` exactly once, idempotently.

    If the target already exists with the same content hash this is a
    no-op; a different content under the same content-addressed path is
    a ``dedup_collision`` (a corrupted store, not a platform drift).
    """
    target = dedup_target(dump_root, hash_hex, ext, kind.value)
    try:
        if target.exists():
            existing = target.read_bytes()
            if sha256_hex(existing) != hash_hex:
                return Err(CliError(kind=CliErrorKind.INTERNAL, subject="dedup_collision"))
            log.info("dedup hit %s", target)
            return Ok(target)
        # Create and enforce each level: _common/, <kind>/, <hash[:2]>/.
        current = dump_root
        for part in ("_common", kind.value, hash_hex[:2]):
            current = current / part
            ensure_private_dir(current)
        _write_bytes(target, data)
    except OSError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="media_write"))
    log.info("wrote %s", target)
    return Ok(target)


def link_media_to_post(
    post_dir: Path,
    dedup_path: Path,
    kind: MediaItemKind,
    n: int,
    *,
    log: logging.Logger,
) -> CliResult[Path]:
    """Link ``post_dir/<subdir>/<n><ext>`` to the shared store entry.

    The symlink is relative so the dump tree stays movable. On a
    filesystem that rejects symlinks the file is copied instead.
    """
    subdir = _post_dir_kind(kind)
    link = post_dir / subdir / f"{n}{dedup_path.suffix}"
    rel_target = os.path.relpath(dedup_path, link.parent)
    try:
        ensure_private_dir(link.parent)
        if link.exists() or link.is_symlink():
            link.unlink()
        try:
            link.symlink_to(rel_target)
        except OSError:
            log.info("symlink unsupported for %s; copying", link)
            shutil.copy2(dedup_path, link)
    except OSError:
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="media_link"))
    log.info("linked %s -> %s", link, rel_target)
    return Ok(link)


__all__ = [
    "ensure_private_dir",
    "link_media_to_post",
    "resolve_post_dir",
    "write_deduped_media",
    "write_post",
]
