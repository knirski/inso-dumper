"""Offline dump audit: ``verify`` and ``materialize`` (Phase 6).

The ``_common/`` store is content-addressed — the blob filename stem
*is* the sha256 hex (``layout.dedup_target``) — so verification is
self-contained: re-hash every blob and compare with its own path, and
resolve every symlink in the tree (a missing target means the blob it
pointed to is gone). No checksums are stored anywhere else, so this
walk is the whole truth.

``materialize`` makes the per-child/per-conversation tree self-contained
by replacing each resolvable symlink with a real copy. ``_common/`` is
never touched: future syncs still dedup against it, and ``index`` keeps
working. Dangling links are skipped with a warning — ``verify`` is the
tool that reports them, the human decides.

Pure part: the report dataclasses. Shell part: the read-only /
write-boundary walks, with ``OSError`` converted to ``Err(INTERNAL)``
at the boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.dump.layout import sha256_hex
from inso_dumper.dump.writer import _write_bytes
from inso_dumper.errors import CliError, CliErrorKind, CliResult

log = logging.getLogger("inso_dumper.dump.audit")

_COMMON_KINDS = ("photos", "videos", "attachments")


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Findings from one ``verify`` run; paths are dump-root relative."""

    blobs: int
    corrupt: tuple[str, ...]
    unexpected: tuple[str, ...]  # files under _common/ without a hash name
    links: int
    dangling: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.corrupt or self.unexpected or self.dangling)


@dataclass(frozen=True, slots=True)
class MaterializeReport:
    copied: int
    skipped_dangling: int


def _iter_symlinks(dump_root: Path) -> list[Path]:
    """Every symlink in the tree (not followed), in a stable order."""
    return sorted(p for p in dump_root.rglob("*") if p.is_symlink())


def _common_blobs(dump_root: Path) -> tuple[list[Path], list[str]]:
    """``_common/`` blob files plus dump-root-relative unexpected names."""
    common = dump_root / "_common"
    if not common.is_dir():
        return [], []
    blobs: list[Path] = []
    unexpected: list[str] = []
    for kind in _COMMON_KINDS:
        kind_dir = common / kind
        if not kind_dir.is_dir():
            continue
        for shard in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            for blob in sorted(shard.iterdir()):
                if blob.is_symlink() or not blob.is_file():
                    unexpected.append(f"_common/{kind}/{shard.name}/{blob.name}")
                    continue
                stem = blob.stem
                if len(stem) != 64 or any(c not in "0123456789abcdef" for c in stem):
                    unexpected.append(f"_common/{kind}/{shard.name}/{blob.name}")
                    continue
                blobs.append(blob)
        for leftover in kind_dir.glob("*"):
            if leftover.is_file():
                unexpected.append(f"_common/{kind}/{leftover.name}")
    return blobs, unexpected


def verify_dump(dump_root: Path, log: logging.Logger) -> CliResult[VerifyReport]:
    """Read-only integrity audit: re-hash every ``_common/`` blob against
    its content-addressed name and resolve every symlink. ``OSError``
    during the scan is ``Err(INTERNAL, 'verify_scan')``."""
    try:
        links = _iter_symlinks(dump_root)
        dangling: list[str] = []
        for link in links:
            if not link.resolve().exists():
                rel = link.relative_to(dump_root).as_posix()
                dangling.append(rel)
                log.warning("dangling symlink: %s", rel)
        blobs, unexpected = _common_blobs(dump_root)
        for name in unexpected:
            log.warning("unexpected file in _common: %s", name)
        corrupt: list[str] = []
        for blob in blobs:
            data = blob.read_bytes()
            if sha256_hex(data) != blob.stem:
                rel = blob.relative_to(dump_root).as_posix()
                corrupt.append(rel)
                log.warning("corrupt blob: %s", rel)
    except OSError as exc:
        log.warning("verify scan failed: %s", exc)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="verify_scan"))
    report = VerifyReport(
        blobs=len(blobs),
        corrupt=tuple(corrupt),
        unexpected=tuple(unexpected),
        links=len(links),
        dangling=tuple(dangling),
    )
    log.info(
        "verified %d blobs (%d corrupt, %d unexpected), %d links (%d dangling)",
        report.blobs,
        len(report.corrupt),
        len(report.unexpected),
        report.links,
        len(report.dangling),
    )
    return Ok(report)


def materialize_dump(dump_root: Path, log: logging.Logger) -> CliResult[MaterializeReport]:
    """Replace every resolvable symlink with a real copy of its target
    (atomic 0o600 write). Dangling links are warned and left in place,
    ``_common/`` is untouched. ``OSError`` is
    ``Err(INTERNAL, 'materialize_write')``."""
    copied = 0
    skipped_dangling = 0
    try:
        for link in _iter_symlinks(dump_root):
            target = link.resolve()
            if not target.exists():
                skipped_dangling += 1
                log.warning("dangling symlink, skipped: %s", link)
                continue
            data = target.read_bytes()
            _write_bytes(link, data)
            copied += 1
            log.info("materialized %s", link)
    except OSError as exc:
        log.warning("materialize failed: %s", exc)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="materialize_write"))
    report = MaterializeReport(copied=copied, skipped_dangling=skipped_dangling)
    log.info("materialized %d links (%d dangling)", report.copied, report.skipped_dangling)
    return Ok(report)


__all__ = ["MaterializeReport", "VerifyReport", "materialize_dump", "verify_dump"]
