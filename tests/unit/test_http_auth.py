"""Unit tests for the pure login parsers."""

from __future__ import annotations

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.auth import build_login_payload, parse_login_response
from inso_dumper.http.client import Response


def test_build_login_payload_matches_spike_form() -> None:
    payload = build_login_payload("user@example.com", "hunter2")
    assert payload == {"_username": "user@example.com", "_password": "hunter2"}


def test_parse_login_response_succeeds_with_phpsessid_and_uuid() -> None:
    response = Response(
        status=200,
        body=b"<html>dashboard</html>",
        headers=(
            (
                "Set-Cookie",
                "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3; path=/; HttpOnly",
            ),
        ),
    )
    final_url = "https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"
    result = parse_login_response(response, final_url=final_url)
    assert isinstance(result, Ok)
    assert result.value.phpsessid == "d9f828d2629433b8d1b9690a17d477e3"
    assert result.value.user_uuid == "eea48660-3740-11ed-a611-06dd2728d782"
    assert result.value.schema_version == 1


def test_parse_login_response_missing_phpsessid_returns_auth_err() -> None:
    response = Response(
        status=200,
        body=b"",
        headers=(("Set-Cookie", "REMEMBERME=deleted; path=/"),),
    )
    final_url = "https://app.inso.pl/panel/home/abc/"
    result = parse_login_response(response, final_url=final_url)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.AUTH
    assert result.error.subject == "missing_phpsessid"


def test_parse_login_response_no_user_uuid_returns_auth_err() -> None:
    response = Response(
        status=200,
        body=b"",
        headers=(
            (
                "Set-Cookie",
                "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3; path=/; HttpOnly",
            ),
        ),
    )
    result = parse_login_response(response, final_url="https://app.inso.pl/login")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.AUTH
    assert result.error.subject == "no_user_uuid"


def test_parse_login_response_invalid_uuid_returns_auth_err() -> None:
    response = Response(
        status=200,
        body=b"",
        headers=(("Set-Cookie", "PHPSESSID=abc; path=/; HttpOnly"),),
    )
    result = parse_login_response(response, final_url="https://app.inso.pl/panel/home/not-a-uuid/")
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.AUTH
    assert result.error.subject == "no_user_uuid"


@pytest.mark.parametrize(
    ("cookie_header", "expected_sid"),
    [
        (
            "PHPSESSID=abcdef; path=/; HttpOnly",
            "abcdef",
        ),
        (
            "REMEMBERME=x; PHPSESSID=xyz123; path=/",
            "xyz123",
        ),
        (
            "PHPSESSID=\"quoted-value\"; path=/",
            "quoted-value",  # surrounding quotes are stripped
        ),
    ],
)
def test_parse_login_response_extracts_phpsessid_from_set_cookie(
    cookie_header: str, expected_sid: str
) -> None:
    response = Response(
        status=200,
        body=b"",
        headers=(("Set-Cookie", cookie_header),),
    )
    result = parse_login_response(
        response,
        final_url="https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782/",
    )
    assert isinstance(result, Ok)
    assert result.value.phpsessid == expected_sid
