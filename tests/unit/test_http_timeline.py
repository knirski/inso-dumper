"""Unit tests for the pure timeline page parser (http/timeline.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.timeline import parse_timeline_page

SPIKE_DIR = Path("docs/api-notes-data")
SPIKE_FILES = [
    ("timeline-posts-page1-category0.json", 10),
    ("timeline-posts-page1-category2.json", 10),
    ("timeline-posts-page1-category3.json", 10),
]


def _spike_bytes(fname: str) -> bytes:
    path = SPIKE_DIR / fname
    if not path.exists():
        pytest.skip(f"spike corpus absent: {path}")
    return path.read_bytes()


@pytest.mark.parametrize(
    ("fname", "expected"),
    SPIKE_FILES,
    ids=["category0", "category2", "category3"],
)
def test_parses_spike_files(fname: str, expected: int) -> None:
    result = parse_timeline_page(_spike_bytes(fname))
    assert isinstance(result, Ok)
    assert len(result.value) == expected
    assert result.value[0].id


def test_empty_items_returns_ok_empty_list() -> None:
    body = json.dumps({"items": [], "waitingToProcess": 0}).encode("utf-8")
    result = parse_timeline_page(body)
    assert isinstance(result, Ok)
    assert result.value == []


def _one_item(fname: str) -> dict[str, Any]:
    return json.loads(_spike_bytes(fname))["items"][0]


def test_bogus_item_key_returns_platform_changed() -> None:
    payload: dict[str, Any] = {
        "items": [_one_item("timeline-posts-page1-category2.json")],
        "waitingToProcess": 0,
    }
    payload["items"][0]["brandNewKey"] = True
    result = parse_timeline_page(json.dumps(payload).encode("utf-8"))
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "timeline_page"


def test_bogus_top_level_key_returns_platform_changed() -> None:
    payload = {"items": [], "waitingToProcess": 0, "surprise": 1}
    result = parse_timeline_page(json.dumps(payload).encode("utf-8"))
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "timeline_page"


def test_malformed_json_returns_platform_changed() -> None:
    result = parse_timeline_page(b"<html>not json</html>")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "timeline_page"


def test_non_object_payload_returns_platform_changed() -> None:
    result = parse_timeline_page(b"[1, 2, 3]")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
