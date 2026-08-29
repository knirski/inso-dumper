"""Unit tests for the Session pydantic model."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from inso_dumper.models.session import Session


def test_session_minimal_round_trip() -> None:
    s = Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )
    assert s.schema_version == 1
    assert s.phpsessid == "d9f828d2629433b8d1b9690a17d477e3"
    assert s.user_uuid == "eea48660-3740-11ed-a611-06dd2728d782"
    assert s.expires_at is None


def test_session_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        cast(Any, Session)(
            phpsessid="abc",
            user_uuid="def",
            extra_field="x",  # type: ignore[call-arg]
        )


def test_session_schema_version_must_be_1() -> None:
    with pytest.raises(ValidationError):
        cast(Any, Session)(  # type: ignore[call-arg]
            schema_version=2,
            phpsessid="abc",
            user_uuid="def",
        )


def test_session_serialization_round_trip() -> None:
    s = Session(phpsessid="x", user_uuid="y")
    json = s.model_dump_json()
    parsed = Session.model_validate_json(json)
    assert parsed == s


def test_session_repr_does_not_leak_phpsessid() -> None:
    """Defense-in-depth: repr() must not include the cookie value."""
    s = Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )
    rendered = repr(s)
    assert "d9f828d2629433b8d1b9690a17d477e3" not in rendered
    assert "user_uuid" in rendered


def test_session_str_does_not_leak_phpsessid() -> None:
    s = Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )
    assert "d9f828d2629433b8d1b9690a17d477e3" not in str(s)
