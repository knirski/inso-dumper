"""Per-child identity and slug derivation.

Pure data. The slug is the directory name under ``dump/children/``; it
must be deterministic and human-readable so re-runs land in the same
place.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SLUG_RE = re.compile(r"[^a-z0-9]+")


_LATIN_FOLD = str.maketrans(
    {
        "ł": "l",
        "Ł": "l",  # lowercase in output; caller will .lower()
        "ą": "a",
        "Ą": "a",
        "ć": "c",
        "Ć": "c",
        "ę": "e",
        "Ę": "e",
        "ń": "n",
        "Ń": "n",
        "ó": "o",
        "Ó": "o",
        "ś": "s",
        "Ś": "s",
        "ź": "z",
        "Ź": "z",
        "ż": "z",
        "Ż": "z",
    }
)


def _ascii_fold(text: str) -> str:
    """Fold Polish (and similar Latin-extension) diacritics to ASCII.

    NFKD handles combining marks; the explicit map handles non-decomposable
    single-codepoint letters like ``ł``. Anything outside the map and
    outside Latin-1 falls through and the slug regex turns it into a
    hyphen (e.g. Cyrillic, CJK).
    """
    normalized = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in normalized if not unicodedata.combining(c))
    return folded.translate(_LATIN_FOLD)


@dataclass(frozen=True, slots=True)
class Child:
    """A child listed on the parent's account."""

    child_id: str
    first_name: str
    last_name: str
    group: str | None
    avatar_color: str
    initials: str

    @property
    def slug(self) -> str:
        first = _ascii_fold(self.first_name).lower()
        last_initial = _ascii_fold(self.last_name[:1]).lower() if self.last_name else ""
        base = f"{first}-{last_initial}" if last_initial else first
        slug = _SLUG_RE.sub("-", base).strip("-")
        return slug or "child"

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
