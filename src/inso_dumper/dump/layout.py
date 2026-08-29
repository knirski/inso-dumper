"""Pure dump-path derivation, content hashing, dedup targets.

Functional core: no IO — every function takes or returns values only.
The writer owns existence checks, collision suffixing, and partial-run
recovery because those need disk access; this module only computes the
canonical paths so the layout rules live in exactly one place.

The post slug shares the children's slug rule (``_SLUG_RE`` +
``_ascii_fold`` from ``models/children.py``) so a slug derived for the
same post is identical across runs and across the codebase.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from inso_dumper.models.children import _SLUG_RE, _ascii_fold
from inso_dumper.models.timeline import Post


def _slugify(title: str) -> str:
    return _SLUG_RE.sub("-", _ascii_fold(title).lower()).strip("-")


def derive_post_slug(post: Post) -> str:
    """Canonical per-post slug: ``<YYYY-MM-DD>-<title-slug>``."""
    date = post.created_at.date().isoformat()
    slug = _slugify(post.title)
    return f"{date}-{slug}" if slug else date


def post_target_dir(dump_root: Path, child_slug: str, post: Post) -> Path:
    """The per-post directory (collision suffixes are the writer's job)."""
    return dump_root / child_slug / "announcements" / derive_post_slug(post)


def dedup_target(dump_root: Path, hash_hex: str, ext: str, kind: str) -> Path:
    """The ``_common/`` storage path for one media file by content hash.

    ``kind`` names the subtree: ``photos``, ``videos``, or
    ``attachments``. ``ext`` includes the leading dot.
    """
    return dump_root / "_common" / kind / hash_hex[:2] / f"{hash_hex}{ext}"


def sha256_hex(data: bytes) -> str:
    """The single dedup hash function; the writer never imports hashlib."""
    return hashlib.sha256(data).hexdigest()


__all__ = ["dedup_target", "derive_post_slug", "post_target_dir", "sha256_hex"]
