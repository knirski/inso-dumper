"""Unit tests for the drive models and listing parser (T3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.drive import parse_drive_listing
from inso_dumper.models.drive import DriveBreadcrumb, DriveListing

SPIKE_DIR = Path("docs/api-notes-data")
DRIVE_SAMPLE = SPIKE_DIR / "drive-items-sample.json"


def _drive_body() -> bytes:
    if not DRIVE_SAMPLE.exists():
        pytest.skip(f"spike corpus absent: {DRIVE_SAMPLE}")
    return DRIVE_SAMPLE.read_bytes()


def _empty_listing() -> bytes:
    return (
        b'{"breadcrumbs": [{"name": "Pliki", "id": null}],'
        b' "directories": [], "directory": null, "files": []}'
    )


# --- models -------------------------------------------------------------------


def test_drive_breadcrumb_accepts_nullable_id() -> None:
    """The captured root breadcrumb is {name: 'Pliki', id: null}."""
    bc = DriveBreadcrumb.model_validate({"name": "Pliki", "id": None})
    assert bc.name == "Pliki"
    assert bc.id is None


def test_drive_listing_matches_captured_envelope() -> None:
    listing = DriveListing.model_validate(
        {
            "breadcrumbs": [{"name": "Pliki", "id": None}],
            "directories": [],
            "directory": None,
            "files": [],
        }
    )
    assert listing.breadcrumbs[0].name == "Pliki"
    assert listing.directories == []
    assert listing.files == []
    assert listing.directory is None


def test_drive_listing_permissive_fields_accept_arbitrary_shapes() -> None:
    """directories/files shapes are provisional (empty drive at spike
    time) — the model stays permissive there until a non-empty capture."""
    listing = DriveListing.model_validate(
        {
            "breadcrumbs": [],
            "directories": [{"anything": 1}],
            "directory": {"alsoAnything": True},
            "files": ["even", 1, "this"],
        }
    )
    assert listing.directories == [{"anything": 1}]
    assert listing.files == ["even", 1, "this"]


def test_drive_listing_unknown_envelope_key_raises() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DriveListing.model_validate(
            {"breadcrumbs": [], "directories": [], "directory": None, "files": [], "x": 1}
        )


def test_drive_breadcrumb_unknown_key_raises() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DriveBreadcrumb.model_validate({"name": "a", "id": "b", "x": 1})


# --- parser -------------------------------------------------------------------


def test_parse_drive_listing_spike_capture() -> None:
    result = parse_drive_listing(_drive_body())
    assert isinstance(result, Ok)
    assert result.value.breadcrumbs[0].name == "Pliki"


def test_parse_drive_listing_empty_ok() -> None:
    result = parse_drive_listing(_empty_listing())
    assert isinstance(result, Ok)


def test_parse_drive_listing_unknown_key_is_platform_changed() -> None:
    result = parse_drive_listing(_empty_listing().replace(b"files", b"brandNewKey", 1))
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "drive_listing"


def test_parse_drive_listing_malformed_json_is_platform_changed() -> None:
    result = parse_drive_listing(b"{nope")
    assert isinstance(result, Err)
    assert result.error.subject == "drive_listing"
