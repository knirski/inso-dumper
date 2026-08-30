"""Drive domain models (provisional): DriveBreadcrumb, DriveListing.

Functional core: no IO, no httpx. Only ``breadcrumbs`` is verified
against the Aug 2026 spike capture (the spike account's drive was
empty); the other fields are permissive placeholders so the envelope
validates without inventing unverified node shapes — see design
Open Question 1. Tighten them from a non-empty capture before any
documents dumping goes live.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DriveBreadcrumb(BaseModel):
    """One verified breadcrumb entry: root carries ``id: null``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    id: str | None


class DriveListing(BaseModel):
    """Typed mirror of the ``/drive/items`` envelope.

    ``breadcrumbs`` is strict. The remaining fields are provisional:
    they validate anything so the captured (empty-drive) envelope is
    accepted, but no dumper code may rely on their shapes until the
    gated spike task pins them from a non-empty drive capture.
    """

    model_config = ConfigDict(extra="forbid")

    breadcrumbs: list[DriveBreadcrumb]
    # Provisional (unverified shapes) — see the class docstring.
    directories: list[object]
    directory: object | None
    files: list[object]


__all__ = ["DriveBreadcrumb", "DriveListing"]
